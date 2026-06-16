# analyze_local.py

"""Analyze unprocessed Reddit items already stored in SQLite using local chat completions.

Run examples:
    python analyze_local.py
    python analyze_local.py --limit 50
    python analyze_local.py --threshold 6.5
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, UTC

from config.config_loader import get_config
from db.reader import get_post_parent_mapping
from db.writer import (
    mark_insight_processed,
    mark_posts_in_history,
    update_post_analysis_status,
    update_post_filter_decision,
    update_post_filter_scores,
    update_post_insight,
    update_post_prefilter_result,
)
from gpt.filters import build_filter_prompt, build_prefilter_prompt
from gpt.insights import build_insight_prompt
from gpt.local_chat import chat_markdown, parse_filter_markdown, parse_insight_markdown, parse_prefilter_markdown
from scheduler.runner import is_valid_post
from utils.helpers import ensure_directory_exists, sanitize_text
from utils.logger import setup_logger

log = setup_logger()
config = get_config()
RESULT_DIR = "data/batch_responses"


def _connect():
    conn = sqlite3.connect(config["database"]["path"])
    conn.row_factory = sqlite3.Row
    return conn


def get_unprocessed_posts(limit: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM posts
            WHERE id NOT IN (SELECT id FROM history)
              AND (insight_processed IS NULL OR insight_processed = 0)
              AND COALESCE(analysis_status, 'pending') = 'pending'
            ORDER BY created_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _format_for_prompt(post: dict) -> dict:
    return {
        "title": sanitize_text(post.get("title", "")),
        "body": sanitize_text(post.get("body", "")),
        "post_body": sanitize_text(post.get("post_body", "")),
    }


def _weighted_score(scores: dict) -> float:
    weights = config["scoring"]
    return (
        scores.get("relevance_score", 0) * weights.get("relevance_weight", 0)
        + scores.get("emotional_intensity", 0) * weights.get("emotion_weight", 0)
        + scores.get("pain_point_clarity", 0) * weights.get("pain_point_weight", 0)
        + scores.get("implementability_score", 5) * weights.get("implementability_weight", 0)
        + scores.get("technical_depth_score", 5) * weights.get("technical_depth_weight", 0)
    )


def _write_jsonl(path: str, row: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _openai_style_result(custom_id: str, content: dict) -> dict:
    return {
        "custom_id": custom_id,
        "response": {
            "body": {
                "choices": [
                    {"message": {"content": json.dumps(content, ensure_ascii=False)}}
                ]
            }
        },
    }


def dedupe_by_thread(candidate_scores: dict[str, float]) -> set[str]:
    parent_mapping = get_post_parent_mapping(set(candidate_scores.keys()))
    thread_best = {}
    for post_id, score in candidate_scores.items():
        thread_id = parent_mapping.get(post_id) or post_id
        if thread_id not in thread_best or score > thread_best[thread_id][1]:
            thread_best[thread_id] = (post_id, score)
    return {post_id for post_id, _ in thread_best.values()}


def analyze(limit: int, threshold: float, progress_callback=None, stop_callback=None):
    ensure_directory_exists(RESULT_DIR)
    raw_posts = get_unprocessed_posts(limit)
    invalid_ids = [p["id"] for p in raw_posts if not is_valid_post(p)]
    if invalid_ids:
        for post_id in invalid_ids:
            update_post_analysis_status(post_id, "invalid", "Missing title or body.")
        mark_posts_in_history(invalid_ids)
        log.info(f"Marked {len(invalid_ids)} invalid empty-title/body items as processed in history.")
    posts = [p for p in raw_posts if is_valid_post(p)]
    log.info(f"Loaded {len(posts)} unprocessed posts/comments for analysis.")
    if progress_callback:
        progress_callback("filtering", 0, len(posts))
    if not posts:
        return

    provider = config["ai"]["provider"]
    model_filter = config["ai"][provider]["model_filter"]
    model_deep = config["ai"][provider]["model_deep"]
    min_depth = config["scoring"].get("min_technical_depth", 4)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    prefilter_result_path = os.path.join(RESULT_DIR, f"prefilter_result_local_{ts}.jsonl")
    filter_result_path = os.path.join(RESULT_DIR, f"filter_result_local_{ts}.jsonl")
    insight_result_path = os.path.join(RESULT_DIR, f"insight_result_local_{ts}.jsonl")

    candidates = {}

    for index, post in enumerate(posts, start=1):
        if stop_callback and stop_callback():
            log.info("Analysis stop requested during filtering.")
            return
        post_id = post["id"]
        log.info(f"Filtering {index}/{len(posts)}: {post_id} ({post.get('type')}, r/{post.get('subreddit')})")
        try:
            prefilter = chat_markdown(
                build_prefilter_prompt(_format_for_prompt(post)),
                model=model_filter,
                max_tokens=180,
                temperature=0,
                parser=parse_prefilter_markdown,
            )
            _write_jsonl(prefilter_result_path, _openai_style_result(post_id, prefilter))
            update_post_prefilter_result(post_id, bool(prefilter.get("pass")), prefilter.get("reason"))
            if not prefilter.get("pass"):
                update_post_analysis_status(post_id, "filtered_out", prefilter.get("reason") or "Rejected by AI prefilter.")
                mark_posts_in_history([post_id])
                log.info(f"Prefilter rejected: {post_id}, reason={prefilter.get('reason', '')}")
                if progress_callback:
                    progress_callback("filtering", index, len(posts))
                continue

            scores = chat_markdown(
                build_filter_prompt(_format_for_prompt(post)),
                model=model_filter,
                max_tokens=800,
                temperature=0,
                parser=parse_filter_markdown,
            )
            update_post_filter_scores(post_id, scores)
            _write_jsonl(filter_result_path, _openai_style_result(post_id, scores))

            technical_depth = scores.get("technical_depth_score", 5)
            score = _weighted_score(scores)
            passed_full_filter = technical_depth >= min_depth and score >= threshold
            update_post_filter_decision(post_id, passed_full_filter)
            if passed_full_filter:
                candidates[post_id] = score
                log.info(f"Candidate: {post_id}, score={score:.2f}")
            else:
                update_post_analysis_status(post_id, "filtered_out")
                mark_posts_in_history([post_id])
                log.info(f"Rejected: {post_id}, score={score:.2f}, tech_depth={technical_depth}")
            if progress_callback:
                progress_callback("filtering", index, len(posts))
        except Exception as e:
            log.error(f"Failed to filter {post_id}: {e}")
            update_post_analysis_status(post_id, "filter_failed", str(e))
            if progress_callback:
                progress_callback("filtering", index, len(posts))

    high_potential_ids = dedupe_by_thread(candidates)
    duplicate_candidate_ids = set(candidates) - high_potential_ids
    if duplicate_candidate_ids:
        for post_id in duplicate_candidate_ids:
            update_post_analysis_status(post_id, "filtered_out", "Duplicate item in the same thread.")
        mark_posts_in_history(list(duplicate_candidate_ids))
        log.info(f"Marked {len(duplicate_candidate_ids)} duplicate same-thread candidates as processed in history.")

    if not high_potential_ids:
        log.info("No high-value posts found.")
        if progress_callback:
            progress_callback("completed", len(posts), len(posts))
        return

    by_id = {p["id"]: p for p in posts}
    log.info(f"Generating deep insights for {len(high_potential_ids)} items.")
    if progress_callback:
        progress_callback("insight", 0, len(high_potential_ids))

    for index, post_id in enumerate(high_potential_ids, start=1):
        if stop_callback and stop_callback():
            log.info("Analysis stop requested during insight generation.")
            return
        post = by_id.get(post_id)
        if not post:
            continue
        log.info(f"Insight {index}/{len(high_potential_ids)}: {post_id}")
        try:
            insight = chat_markdown(
                build_insight_prompt(_format_for_prompt(post)),
                model=model_deep,
                max_tokens=1500,
                temperature=0,
                parser=parse_insight_markdown,
            )
            update_post_insight(post_id, insight)
            mark_insight_processed(post_id)
            update_post_analysis_status(post_id, "with_insight")
            mark_posts_in_history([post_id])
            _write_jsonl(insight_result_path, _openai_style_result(post_id, insight))
            if progress_callback:
                progress_callback("insight", index, len(high_potential_ids))
        except Exception as e:
            log.error(f"Failed to generate insight for {post_id}: {e}")
            update_post_analysis_status(post_id, "insight_failed", str(e))
            if progress_callback:
                progress_callback("insight", index, len(high_potential_ids))

    log.info("Analysis completed.")
    if progress_callback:
        progress_callback("completed", len(high_potential_ids), len(high_potential_ids))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(config.get("public_scraper", {}).get("max_items_to_analyze", 12)))
    parser.add_argument("--threshold", type=float, default=7.0)
    args = parser.parse_args()
    analyze(limit=args.limit, threshold=args.threshold)
