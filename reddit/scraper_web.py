"""Best-effort HTML scraper for Reddit listing pages.

This is a fallback for environments where Reddit public JSON returns 403.
It reads old.reddit.com listing HTML and extracts post rows. It is less
complete than the JSON/API scrapers and currently does not fetch comments.
"""

import datetime
import html
import re
import time
from html.parser import HTMLParser

import requests

from config.config_loader import get_config
from db.reader import is_already_processed
from db.writer import insert_post
from reddit.rate_limiter import RedditRateLimiter
from utils.logger import setup_logger

log = setup_logger()
config = get_config()

PUBLIC_CFG = config.get("public_scraper", {})
POLITE_DELAY_SECONDS = float(PUBLIC_CFG.get("polite_delay_seconds", 3))

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

limiter = RedditRateLimiter(config["scraper"].get("rate_limit_per_minute", 12))


class OldRedditListingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.posts = []
        self._current = None
        self._capture_title = False
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())

        if tag == "div" and "thing" in classes:
            post_id = (attrs.get("data-fullname") or "").replace("t3_", "")
            subreddit = attrs.get("data-subreddit") or ""
            if post_id:
                self._current = {
                    "id": post_id,
                    "subreddit": subreddit,
                    "created_utc": None,
                    "url": "",
                    "title": "",
                }

        if self._current and tag == "a" and "title" in classes:
            self._current["url"] = attrs.get("href") or ""
            self._capture_title = True
            self._title_parts = []

        if self._current and tag == "time" and attrs.get("datetime"):
            try:
                dt = datetime.datetime.fromisoformat(attrs["datetime"].replace("Z", "+00:00"))
                self._current["created_utc"] = dt.timestamp()
            except ValueError:
                pass

    def handle_data(self, data):
        if self._capture_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag):
        if self._capture_title and tag == "a":
            self._current["title"] = html.unescape("".join(self._title_parts).strip())
            self._capture_title = False

        if tag == "div" and self._current:
            if self._current.get("title"):
                self.posts.append(self._current)
            self._current = None


def _extract_posts_from_html(text: str) -> list[dict]:
    posts = []
    thing_pattern = re.compile(
        r'<div[^>]+class="[^"]*\bthing\b[^"]*"[^>]*data-fullname="t3_([^"]+)"[^>]*>(.*?)(?=<div[^>]+class="[^"]*\bthing\b|<div class="clearleft"|</body>)',
        re.IGNORECASE | re.DOTALL,
    )
    for match in thing_pattern.finditer(text):
        post_id = match.group(1)
        chunk = match.group(2)
        subreddit_match = re.search(r'data-subreddit="([^"]+)"', match.group(0), re.IGNORECASE)
        title_match = re.search(
            r'<a[^>]+class="[^"]*\btitle\b[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            chunk,
            re.IGNORECASE | re.DOTALL,
        )
        if not title_match:
            continue
        time_match = re.search(r'<time[^>]+datetime="([^"]+)"', chunk, re.IGNORECASE)
        created_utc = None
        if time_match:
            try:
                dt = datetime.datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
                created_utc = dt.timestamp()
            except ValueError:
                created_utc = None
        title = re.sub(r"<[^>]+>", "", title_match.group(2))
        posts.append({
            "id": post_id,
            "subreddit": html.unescape(subreddit_match.group(1)) if subreddit_match else "",
            "created_utc": created_utc,
            "url": html.unescape(title_match.group(1)),
            "title": html.unescape(title).strip(),
        })
    return posts


def _is_in_age_range(created_utc: float | None, min_days: int, max_days: int) -> bool:
    if not created_utc:
        return True
    created = datetime.datetime.fromtimestamp(created_utc, tz=datetime.UTC)
    age_days = (datetime.datetime.now(datetime.UTC) - created).days
    return min_days <= age_days <= max_days


def fetch_posts_from_subreddit_web(subreddit: str, limit: int = 10) -> list[dict]:
    min_days = config["scraper"]["min_post_age_days"]
    max_days = config["scraper"]["max_post_age_days"]
    sorts = PUBLIC_CFG.get("sorts", ["hot", "top", "new"])

    results = []
    seen = set()

    for sort in sorts:
        limiter.wait()
        url = f"https://old.reddit.com/r/{subreddit}/{sort}/"
        params = {"limit": limit}
        if sort == "top":
            params["t"] = PUBLIC_CFG.get("top_time_filter", "week")

        try:
            log.info(f"HTML fetch r/{subreddit}/{sort}")
            resp = SESSION.get(url, params=params, timeout=25)
            if resp.status_code != 200:
                log.warning(f"Reddit HTML returned {resp.status_code}: {url}")
                continue
        except Exception as e:
            log.warning(f"Failed to fetch Reddit HTML {url}: {e}")
            continue

        parser = OldRedditListingParser()
        parser.feed(resp.text)
        parsed_posts = parser.posts or _extract_posts_from_html(resp.text)
        if not parsed_posts:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            page_title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)).strip()) if title_match else ""
            log.warning(f"Reddit HTML parsed 0 posts from {url}; status=200; title={page_title!r}; length={len(resp.text)}")
        for parsed in parsed_posts:
            post_id = parsed["id"]
            if post_id in seen or is_already_processed(post_id):
                continue
            seen.add(post_id)

            created_utc = parsed.get("created_utc") or time.time()
            if not _is_in_age_range(created_utc, min_days, max_days):
                continue

            post_url = parsed.get("url") or f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
            if post_url.startswith("/"):
                post_url = f"https://www.reddit.com{post_url}"

            results.append({
                "id": post_id,
                "title": parsed["title"],
                "body": parsed["title"],
                "created_utc": created_utc,
                "subreddit": parsed.get("subreddit") or subreddit,
                "url": post_url,
                "type": "post",
            })

    log.info(f"HTML r/{subreddit}: {len(results)} items")
    return results


def scrape_subreddits_web(stop_callback=None, progress_callback=None) -> list[dict]:
    primary_subreddits = config["subreddits"]["primary"]
    total_limit = config["scraper"].get("max_items_per_day", 10)
    per_subreddit = max(1, total_limit // max(1, len(primary_subreddits)))
    per_subreddit = max(per_subreddit, int(PUBLIC_CFG.get("min_posts_per_subreddit", 2)))

    all_items = []
    log.info(f"Scraping {len(primary_subreddits)} subreddits via Reddit HTML...")
    for index, sub in enumerate(primary_subreddits, start=1):
        if stop_callback and stop_callback():
            log.info("HTML crawl stop requested.")
            break
        if progress_callback:
            progress_callback("crawl", index, len(primary_subreddits))

        items = fetch_posts_from_subreddit_web(sub, limit=per_subreddit)
        for item in items:
            insert_post(item, community_type="primary")
        all_items.extend(items)
        time.sleep(POLITE_DELAY_SECONDS)

    log.info(f"Total HTML items scraped: {len(all_items)}")
    return all_items
