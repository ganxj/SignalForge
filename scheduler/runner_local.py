# scheduler/runner_local.py

"""Local-model pipeline that uses /v1/chat/completions instead of Batch API.

Run with:
    python run_local.py
"""

import json
import os
from datetime import datetime, UTC

from config.config_loader import get_config
from db.cleaner import clean_old_entries
from db.reader import get_post_parent_mapping
from db.schema import create_tables
from db.writer import (
    insert_post,
    mark_insight_processed,
    mark_posts_in_history,
    update_post_filter_scores,
    update_post_insight,
)
from gpt.filters import build_filter_prompt
from gpt.insights import build_insight_prompt
from gpt.local_chat import chat_json
from reddit.scraper_public import scrape_subreddits_public
from scheduler.cost_tracker import initialize_cost_tracking
from scheduler.runner import clean_old_batch_files, is_valid_post
from utils.helpers import ensure_directory_exists, sanitize_text
from utils.logger import setup_logger

log = setup_logger()
config = get_config()

RESULT_DIR = "data/batch_responses"


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


def run_local_pipeline(score_threshold: float = 7.0):
    log.info("Starting local Reddit scraping and analysis pipeline")

    ensure_directory_exists("data")
    ensure_directory_exists(RESULT_DIR)
    ensure_directory_exists("logs")
    clean_old_batch_files()
    create_tables()
    initialize_cost_tracking()

    log.info("Step 1: Cleaning old database entries...")
    clean_old_entries()

    log.info("Step 2: Scraping Reddit posts...")
    scraped_posts = scrape_subreddits_public()
    scraped_posts = [p for p in scraped_posts if is_valid_post(p)]
    max_items_to_analyze = config.get("public_scraper", {}).get("max_items_to_analyze")
    if max_items_to_analyze:
        scraped_posts = scraped_posts[:int(max_items_to_analyze)]
    log.info(f"{len(scraped_posts)} posts remain after sanitization/validation.")

    if not scraped_posts:
        log.warning("No valid posts found. Check Reddit API credentials or scraper settings.")
        return

    provider = config["ai"]["provider"]
    model_filter = config["ai"][provider]["model_filter"]
    model_deep = config["ai"][provider]["model_deep"]
    min_depth = config["scoring"].get("min_technical_depth", 4)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filter_result_path = os.path.join(RESULT_DIR, f"filter_result_local_{ts}.jsonl")
    insight_result_path = os.path.join(RESULT_DIR, f"insight_result_local_{ts}.jsonl")

    log.info("Step 3: Filtering with local model...")
    candidates = {}
    all_filtered_ids = set()

    for index, post in enumerate(scraped_posts, start=1):
        post_id = post["id"]
        log.info(f"Filtering {index}/{len(scraped_posts)}: {post_id}")
        try:
            scores = chat_json(
                build_filter_prompt(_format_for_prompt(post)),
                model=model_filter,
                max_tokens=800,
                temperature=0,
            )
            update_post_filter_scores(post_id, scores)
            _write_jsonl(filter_result_path, _openai_style_result(post_id, scores))
            all_filtered_ids.add(post_id)

            technical_depth = scores.get("technical_depth_score", 5)
            score = _weighted_score(scores)
            if technical_depth >= min_depth and score >= score_threshold:
                candidates[post_id] = score
                log.info(f"Candidate: {post_id}, score={score:.2f}")
            else:
                log.info(f"Rejected: {post_id}, score={score:.2f}, tech_depth={technical_depth}")
        except Exception as e:
            log.error(f"Failed to filter {post_id}: {e}")

    high_potential_ids = dedupe_by_thread(candidates)
    below_threshold_ids = all_filtered_ids - high_potential_ids
    if below_threshold_ids:
        mark_posts_in_history(list(below_threshold_ids))

    if not high_potential_ids:
        log.info("No high-value posts found. Exiting pipeline.")
        return

    log.info(f"Step 4: Deep insight for {len(high_potential_ids)} posts...")
    by_id = {p["id"]: p for p in scraped_posts}

    for index, post_id in enumerate(high_potential_ids, start=1):
        post = by_id.get(post_id)
        if not post:
            continue
        log.info(f"Insight {index}/{len(high_potential_ids)}: {post_id}")
        try:
            insight = chat_json(
                build_insight_prompt(_format_for_prompt(post)),
                model=model_deep,
                max_tokens=1500,
                temperature=0,
            )
            update_post_insight(post_id, insight)
            mark_insight_processed(post_id)
            mark_posts_in_history([post_id])
            _write_jsonl(insight_result_path, _openai_style_result(post_id, insight))
        except Exception as e:
            log.error(f"Failed to generate insight for {post_id}: {e}")

    log.info("Local pipeline completed.")
