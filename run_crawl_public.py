# run_crawl_public.py

"""Crawl Reddit public JSON only, without running AI analysis.

Run:
    python run_crawl_public.py
"""

from db.cleaner import clean_old_entries
from db.schema import create_tables
from reddit.scraper_public import scrape_subreddits_public
from scheduler.cost_tracker import initialize_cost_tracking
from scheduler.runner import clean_old_batch_files
from utils.helpers import ensure_directory_exists
from utils.logger import setup_logger


if __name__ == "__main__":
    log = setup_logger()
    try:
        ensure_directory_exists("data")
        ensure_directory_exists("data/batch_responses")
        ensure_directory_exists("logs")
        clean_old_batch_files()
        create_tables()
        initialize_cost_tracking()
        clean_old_entries()

        items = scrape_subreddits_public()
        log.info(f"Crawl completed. Inserted/seen {len(items)} scraped items.")
    except KeyboardInterrupt:
        log.info("Crawl interrupted by user.")
    except Exception as e:
        log.exception(f"Crawl failed: {e}")
        raise
