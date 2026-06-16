# reddit/scraper_public.py

"""Reddit scraper without Reddit API credentials.

Uses Reddit's public JSON endpoints politely:
    https://www.reddit.com/r/{subreddit}/{sort}.json
    https://www.reddit.com/r/{subreddit}/comments/{post_id}.json

Rules for safer MVP crawling:
- no concurrency
- conservative rate limit
- disk cache to avoid repeated requests
- exponential backoff on 429/403/5xx
- stop current run after repeated rate-limit responses
"""

import datetime
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from config.config_loader import get_config
from db.reader import is_already_processed
from db.writer import insert_post
from reddit.rate_limiter import RedditRateLimiter
from reddit.subreddit_selection import get_crawl_subreddits
from utils.logger import setup_logger

log = setup_logger()
config = get_config()

PUBLIC_CFG = config.get("public_scraper", {})
CACHE_DIR = Path(PUBLIC_CFG.get("cache_dir", "data/cache/reddit_json"))
CACHE_TTL_SECONDS = int(PUBLIC_CFG.get("cache_ttl_hours", 12) * 3600)
MAX_REQUESTS_PER_RUN = int(PUBLIC_CFG.get("max_requests_per_run", 60))
BACKOFF_BASE_SECONDS = int(PUBLIC_CFG.get("backoff_base_seconds", 60))
BACKOFF_MAX_SECONDS = int(PUBLIC_CFG.get("backoff_max_seconds", 900))
MAX_RATE_LIMIT_HITS = int(PUBLIC_CFG.get("max_rate_limit_hits", 2))
MAX_COMMENTS_PER_POST = int(PUBLIC_CFG.get("max_comments_per_post", 30))
COMMENT_SORT = PUBLIC_CFG.get("comment_sort", "confidence")
POLITE_DELAY_SECONDS = float(PUBLIC_CFG.get("polite_delay_seconds", 2.5))

REQUEST_COUNT = 0
RATE_LIMIT_HITS = 0

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({
    "User-Agent": config.get("reddit", {}).get("user_agent")
    or "script:reddit-demand-intel:v0.1 (by /u/local-user)",
    "Accept": "application/json,text/plain,*/*",
})

limiter = RedditRateLimiter(config["scraper"].get("rate_limit_per_minute", 20))


class PublicScraperStopped(RuntimeError):
    pass


def _cache_key(url: str, params: dict | None = None) -> Path:
    params = params or {}
    canonical = url + "?" + urlencode(sorted(params.items()))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _load_cache(path: Path) -> Any | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(path: Path, data: Any):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.debug(f"Failed to save cache {path}: {e}")


def _sleep_polite():
    time.sleep(POLITE_DELAY_SECONDS + random.uniform(0, 1.5))


def _get_json(url: str, params: dict | None = None, use_cache: bool = True) -> Any | None:
    global REQUEST_COUNT, RATE_LIMIT_HITS

    params = params or {}
    cache_path = _cache_key(url, params)
    if use_cache:
        cached = _load_cache(cache_path)
        if cached is not None:
            log.debug(f"Cache hit: {url}")
            return cached

    if REQUEST_COUNT >= MAX_REQUESTS_PER_RUN:
        raise PublicScraperStopped(f"Reached max_requests_per_run={MAX_REQUESTS_PER_RUN}")
    if RATE_LIMIT_HITS >= MAX_RATE_LIMIT_HITS:
        raise PublicScraperStopped(f"Reached max_rate_limit_hits={MAX_RATE_LIMIT_HITS}")

    limiter.wait()
    _sleep_polite()
    REQUEST_COUNT += 1

    for attempt in range(3):
        try:
            resp = SESSION.get(url, params=params, timeout=25)
            status = resp.status_code

            if status == 200:
                data = resp.json()
                _save_cache(cache_path, data)
                return data

            if status in (403, 429):
                RATE_LIMIT_HITS += 1
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait_s = min(int(retry_after), BACKOFF_MAX_SECONDS)
                else:
                    wait_s = min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)
                log.warning(f"Reddit public endpoint returned {status}. Backing off {wait_s}s: {url}")
                time.sleep(wait_s)
                if RATE_LIMIT_HITS >= MAX_RATE_LIMIT_HITS:
                    raise PublicScraperStopped(f"Too many Reddit rate/permission responses: {RATE_LIMIT_HITS}")
                continue

            if 500 <= status < 600:
                wait_s = min(30 * (2 ** attempt), 180)
                log.warning(f"Reddit server error {status}. Backing off {wait_s}s: {url}")
                time.sleep(wait_s)
                continue

            log.warning(f"Reddit public endpoint returned {status}: {url}")
            return None
        except PublicScraperStopped:
            raise
        except Exception as e:
            wait_s = min(15 * (2 ** attempt), 120)
            log.warning(f"Failed to fetch public Reddit JSON {url}: {e}. Retry in {wait_s}s")
            time.sleep(wait_s)

    return None


def _is_in_age_range(created_utc: float, min_days: int, max_days: int) -> bool:
    created = datetime.datetime.fromtimestamp(created_utc, tz=datetime.UTC)
    age_days = (datetime.datetime.now(datetime.UTC) - created).days
    return min_days <= age_days <= max_days


def _flatten_comments(children: list[dict]) -> list[dict]:
    out = []
    stack = list(children or [])
    while stack:
        item = stack.pop(0)
        if item.get("kind") != "t1":
            continue
        data = item.get("data", {})
        out.append(data)
        replies = data.get("replies")
        if isinstance(replies, dict):
            stack.extend(replies.get("data", {}).get("children", []))
    return out


def _fetch_comments(subreddit: str, post_id: str, post_title: str, post_body: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    data = _get_json(url, params={"limit": MAX_COMMENTS_PER_POST, "sort": COMMENT_SORT, "raw_json": 1})
    if not isinstance(data, list) or len(data) < 2:
        return []

    children = data[1].get("data", {}).get("children", [])
    comments = []
    min_days = config["scraper"]["min_post_age_days"]
    max_days = config["scraper"]["max_post_age_days"]

    for c in _flatten_comments(children):
        if len(comments) >= MAX_COMMENTS_PER_POST:
            break
        comment_id = c.get("id")
        body = c.get("body") or ""
        created_utc = c.get("created_utc")
        permalink = c.get("permalink") or f"/r/{subreddit}/comments/{post_id}/"

        if not comment_id or not body or body in ("[deleted]", "[removed]") or not created_utc:
            continue
        if not _is_in_age_range(created_utc, min_days, max_days):
            continue
        if is_already_processed(comment_id):
            continue

        comments.append({
            "id": comment_id,
            "title": post_title,
            "body": body,
            "post_body": post_body,
            "created_utc": created_utc,
            "subreddit": subreddit,
            "url": f"https://www.reddit.com{permalink}",
            "type": "comment",
            "parent_post_id": post_id,
        })

    return comments


def fetch_posts_from_subreddit_public(subreddit: str, limit: int = 10) -> list[dict]:
    min_days = config["scraper"]["min_post_age_days"]
    max_days = config["scraper"]["max_post_age_days"]
    include_comments = config["scraper"].get("include_comments", False)

    seen = set()
    results = []
    sorts = PUBLIC_CFG.get("sorts", ["top", "hot", "new"])

    for sort in sorts:
        url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
        params = {"limit": limit, "raw_json": 1}
        if sort == "top":
            params["t"] = PUBLIC_CFG.get("top_time_filter", "week")
        log.info(f"Public JSON fetch r/{subreddit}/{sort}")
        data = _get_json(url, params=params)
        if not data:
            continue

        children = data.get("data", {}).get("children", [])
        for item in children:
            if item.get("kind") != "t3":
                continue
            p = item.get("data", {})
            post_id = p.get("id")
            if not post_id or post_id in seen:
                continue
            seen.add(post_id)

            title = p.get("title") or ""
            body = p.get("selftext") or ""
            created_utc = p.get("created_utc")
            permalink = p.get("permalink") or f"/r/{subreddit}/comments/{post_id}/"

            if not title or not created_utc:
                continue
            if not _is_in_age_range(created_utc, min_days, max_days):
                continue
            if is_already_processed(post_id):
                continue

            post = {
                "id": post_id,
                "title": title,
                "body": body or title,
                "created_utc": created_utc,
                "subreddit": subreddit,
                "url": f"https://www.reddit.com{permalink}",
                "type": "post",
            }
            results.append(post)

            if include_comments:
                try:
                    comments = _fetch_comments(subreddit, post_id, title, body)
                    results.extend(comments)
                    log.info(f"Fetched {len(comments)} comments for {post_id}")
                except PublicScraperStopped:
                    raise
                except Exception as e:
                    log.warning(f"Failed fetching comments for {post_id}: {e}")

    log.info(f"Public JSON r/{subreddit}: {len(results)} items")
    return results


def scrape_subreddits_public(stop_callback=None, progress_callback=None) -> list[dict]:
    global REQUEST_COUNT, RATE_LIMIT_HITS
    REQUEST_COUNT = 0
    RATE_LIMIT_HITS = 0

    primary_subreddits = get_crawl_subreddits(config)
    total_limit = config["scraper"].get("max_items_per_day", 10)
    per_subreddit = max(1, total_limit // max(1, len(primary_subreddits)))
    per_subreddit = max(per_subreddit, int(PUBLIC_CFG.get("min_posts_per_subreddit", 2)))

    all_items = []
    log.info(f"Scraping {len(primary_subreddits)} subreddits via public JSON...")
    total_subreddits = len(primary_subreddits)
    for index, sub in enumerate(primary_subreddits, start=1):
        if stop_callback and stop_callback():
            log.info("Public JSON crawl stop requested.")
            break
        if progress_callback:
            progress_callback("crawl", index, total_subreddits)
        try:
            items = fetch_posts_from_subreddit_public(sub, limit=per_subreddit)
            for item in items:
                insert_post(item, community_type="primary")
            all_items.extend(items)
            time.sleep(POLITE_DELAY_SECONDS)
        except PublicScraperStopped as e:
            log.warning(f"Skipping r/{sub} after public scraper stop: {e}")
            RATE_LIMIT_HITS = 0
            if REQUEST_COUNT >= MAX_REQUESTS_PER_RUN:
                break
            time.sleep(POLITE_DELAY_SECONDS)

    log.info(f"Total public JSON items scraped: {len(all_items)}; requests={REQUEST_COUNT}; rate_limit_hits={RATE_LIMIT_HITS}")
    return all_items
