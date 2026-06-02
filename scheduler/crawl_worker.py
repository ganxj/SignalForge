"""Standalone crawl worker used by the Streamlit UI.

Running the Reddit browser scraper inside Streamlit's rerun thread makes task
state fragile: a page refresh can lose the in-memory thread context while the
browser process keeps running. This worker keeps crawl execution outside the UI
process and reports progress through data/task_status.json.
"""

import os
import threading

from config.config_loader import get_config
from db.cleaner import clean_old_entries
from db.schema import create_tables
from scheduler.task_state import consume_pending, is_task_stop_requested, set_task_state, utc_now_label
from utils.helpers import ensure_directory_exists
from utils.logger import setup_logger


log = setup_logger()


def start_watchdog(timeout_minutes: int):
    def timeout_exit():
        message = f"Crawl worker exceeded timeout of {timeout_minutes} minutes and was terminated."
        log.error(message)
        set_task_state(
            "crawl",
            running=False,
            last_finished_at=utc_now_label(),
            last_error=message,
            heartbeat_at=utc_now_label(),
            stop_requested=False,
            pid=None,
            pending=False,
        )
        os._exit(124)

    timer = threading.Timer(timeout_minutes * 60, timeout_exit)
    timer.daemon = True
    timer.start()
    return timer


def run_configured_crawl(cfg: dict) -> tuple[str, list[dict]]:
    mode = cfg.get("reddit", {}).get("mode", "public_json")

    ensure_directory_exists("data")
    ensure_directory_exists("logs")
    create_tables()
    clean_old_entries()

    def progress_callback(stage: str, current: int, total: int):
        set_task_state("crawl", stage=stage, current=current, total=total, heartbeat_at=utc_now_label())

    if mode == "api":
        from reddit.scraper import scrape_subreddits

        return mode, scrape_subreddits()

    if mode == "public_json":
        from reddit.scraper_public import scrape_subreddits_public

        return mode, scrape_subreddits_public(
            stop_callback=lambda: is_task_stop_requested("crawl"),
            progress_callback=progress_callback,
        )

    if mode == "html":
        from reddit.scraper_web import scrape_subreddits_web

        return mode, scrape_subreddits_web(
            stop_callback=lambda: is_task_stop_requested("crawl"),
            progress_callback=progress_callback,
        )

    if mode == "browser":
        from reddit.scraper_browser import scrape_subreddits_browser

        return mode, scrape_subreddits_browser(
            stop_callback=lambda: is_task_stop_requested("crawl"),
            progress_callback=progress_callback,
        )

    raise ValueError("Invalid reddit.mode. Use 'public_json', 'html', 'browser', or 'api'.")


def main():
    while True:
        cfg = get_config()
        timeout_minutes = int(cfg.get("public_scraper", {}).get("browser_crawl_timeout_minutes", 25))
        watchdog = start_watchdog(max(1, timeout_minutes))
        now = utc_now_label()
        set_task_state(
            "crawl",
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
            mode_used, items = run_configured_crawl(cfg)
            stopped = is_task_stop_requested("crawl")
            message = f"Crawl stopped via {mode_used}. Scraped {len(items)} items."
            if not stopped:
                message = f"Crawl completed via {mode_used}. Scraped {len(items)} items."
            set_task_state(
                "crawl",
                running=False,
                last_finished_at=utc_now_label(),
                last_message=message,
                last_error="",
                heartbeat_at=utc_now_label(),
                stop_requested=False,
                pid=None,
            )
        except BaseException as e:
            log.exception("Crawl worker failed.")
            set_task_state(
                "crawl",
                running=False,
                last_finished_at=utc_now_label(),
                last_error=f"{type(e).__name__}: {e!r}",
                heartbeat_at=utc_now_label(),
                stop_requested=False,
                pid=None,
            )
            raise
        finally:
            watchdog.cancel()

        if not consume_pending("crawl"):
            break


if __name__ == "__main__":
    main()
