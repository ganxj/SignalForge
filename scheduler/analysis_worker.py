"""Standalone analysis worker used by the Streamlit UI and scheduler."""

import argparse
import os
import sqlite3

from analyze_local import analyze
from config.config_loader import get_config
from scheduler.task_state import consume_pending, is_task_stop_requested, set_task_state, utc_now_label
from utils.helpers import sanitize_text
from utils.logger import setup_logger


log = setup_logger()


def get_analyzable_count(cfg: dict) -> int:
    db_path = cfg["database"]["path"]
    if not os.path.exists(db_path):
        return 0

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT title, body
            FROM posts
            WHERE id NOT IN (SELECT id FROM history)
              AND (insight_processed IS NULL OR insight_processed = 0)
            """
        ).fetchall()
    finally:
        conn.close()

    return sum(1 for title, body in rows if sanitize_text(title) and sanitize_text(body))


def run_once(limit: int, threshold: float):
    def progress_callback(stage: str, current: int, total: int):
        set_task_state("analysis", stage=stage, current=current, total=total, heartbeat_at=utc_now_label())

    analyze(
        limit=limit,
        threshold=threshold,
        progress_callback=progress_callback,
        stop_callback=lambda: is_task_stop_requested("analysis"),
    )


def main():
    cfg = get_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--threshold", type=float, default=float(cfg.get("scoring", {}).get("analysis_threshold", 7.0)))
    args = parser.parse_args()

    while True:
        now = utc_now_label()
        set_task_state(
            "analysis",
            running=True,
            last_started_at=now,
            last_finished_at=None,
            last_error="",
            last_message="",
            stage="",
            current=0,
            total=0,
            heartbeat_at=now,
            stop_requested=False,
            pid=os.getpid(),
        )

        try:
            limit = get_analyzable_count(cfg) if args.all or args.limit is None else args.limit
            run_once(limit, args.threshold)
            message = "Analysis stopped." if is_task_stop_requested("analysis") else "Analysis completed."
            set_task_state(
                "analysis",
                running=False,
                last_finished_at=utc_now_label(),
                last_message=message,
                last_error="",
                stage="completed",
                current=0,
                total=0,
                heartbeat_at=utc_now_label(),
                stop_requested=False,
                pid=None,
            )
        except BaseException as e:
            log.exception("Analysis worker failed.")
            set_task_state(
                "analysis",
                running=False,
                last_finished_at=utc_now_label(),
                last_error=f"{type(e).__name__}: {e!r}",
                heartbeat_at=utc_now_label(),
                stop_requested=False,
                pid=None,
            )
            raise

        if not consume_pending("analysis"):
            break


if __name__ == "__main__":
    main()
