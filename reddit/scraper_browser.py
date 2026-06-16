"""Browser-backed Reddit scraper using an isolated Playwright Chromium instance."""

import datetime
import re
import time
from urllib.parse import urljoin

from config.config_loader import get_config
from db.reader import is_already_processed
from db.writer import insert_post
from reddit.subreddit_selection import get_crawl_subreddits
from utils.logger import setup_logger

log = setup_logger()
config = get_config()
PUBLIC_CFG = config.get("public_scraper", {})
MAX_COMMENTS_PER_POST = int(PUBLIC_CFG.get("max_comments_per_post", 30))
BROWSER_PAGE_TIMEOUT_MS = int(PUBLIC_CFG.get("browser_page_timeout_seconds", 25) * 1000)
BROWSER_SETTLE_MS = int(PUBLIC_CFG.get("browser_settle_milliseconds", 1200))


def _is_in_age_range(created_utc: float, min_days: int, max_days: int) -> bool:
    created = datetime.datetime.fromtimestamp(created_utc, tz=datetime.UTC)
    age_days = (datetime.datetime.now(datetime.UTC) - created).days
    return min_days <= age_days <= max_days


def _post_id_from_url(url: str) -> str | None:
    match = re.search(r"/comments/([A-Za-z0-9_]+)/", url)
    return match.group(1) if match else None


def _normalize_url(url: str) -> str:
    return urljoin("https://www.reddit.com", url)


def _to_old_reddit_url(url: str) -> str:
    normalized = _normalize_url(url)
    return normalized.replace("https://www.reddit.com", "https://old.reddit.com", 1)


def _parse_created_utc(value: str | None, fallback: float | None = None) -> float:
    if not value:
        return fallback or time.time()
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return fallback or time.time()


def _extract_posts_from_page(page, subreddit: str) -> list[dict]:
    posts = []
    seen = set()

    # Prefer old.reddit markup when available.
    old_items = page.locator("div.thing[data-fullname]").all()
    for item in old_items:
        fullname = item.get_attribute("data-fullname") or ""
        post_id = fullname.replace("t3_", "")
        title_link = item.locator("a.title").first
        if not post_id or title_link.count() == 0:
            continue
        title = (title_link.inner_text() or "").strip()
        url = _normalize_url(title_link.get_attribute("href") or "")
        if post_id and title and post_id not in seen:
            seen.add(post_id)
            posts.append({"id": post_id, "title": title, "url": url, "subreddit": subreddit})

    if posts:
        return posts

    # New Reddit/shreddit markup varies; extract comment links and nearby text.
    links = page.locator('a[href*="/comments/"]').all()
    for link in links:
        href = link.get_attribute("href") or ""
        url = _normalize_url(href)
        post_id = _post_id_from_url(url)
        if not post_id or post_id in seen:
            continue

        title = (link.inner_text() or "").strip()
        if not title:
            title = link.get_attribute("aria-label") or link.get_attribute("title") or ""
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 3:
            continue

        seen.add(post_id)
        posts.append({"id": post_id, "title": title, "url": url, "subreddit": subreddit})

    return posts


def fetch_comments_from_post_browser(
    page,
    subreddit: str,
    post: dict,
    limit: int = MAX_COMMENTS_PER_POST,
) -> list[dict]:
    min_days = config["scraper"]["min_post_age_days"]
    max_days = config["scraper"]["max_post_age_days"]
    post_id = post["id"]
    comments_url = _to_old_reddit_url(post["url"])

    try:
        log.info(f"Browser fetch comments for {post_id}")
        page.goto(comments_url, wait_until="domcontentloaded", timeout=BROWSER_PAGE_TIMEOUT_MS)
        page.wait_for_timeout(BROWSER_SETTLE_MS)
    except Exception as e:
        log.warning(f"Failed browser comments fetch {comments_url}: {e}")
        return []

    comments = []
    seen = set()
    comment_items = page.locator("div.commentarea div.thing.comment[data-fullname]").all()

    for item in comment_items:
        if len(comments) >= limit:
            break

        fullname = item.get_attribute("data-fullname") or ""
        comment_id = fullname.replace("t1_", "")
        if not comment_id or comment_id in seen or is_already_processed(comment_id):
            continue
        seen.add(comment_id)

        body_locator = item.locator("div.usertext-body div.md").first
        if body_locator.count() == 0:
            continue
        body = re.sub(r"\s+", " ", (body_locator.inner_text() or "")).strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue

        time_locator = item.locator("time.live-timestamp").first
        created_value = time_locator.get_attribute("datetime") if time_locator.count() > 0 else None
        created_utc = _parse_created_utc(created_value, post.get("created_utc"))
        if not _is_in_age_range(created_utc, min_days, max_days):
            continue

        permalink = ""
        permalink_link = item.locator("ul.buttons a.bylink").first
        if permalink_link.count() > 0:
            permalink = permalink_link.get_attribute("href") or ""

        comments.append({
            "id": comment_id,
            "title": post["title"],
            "body": body,
            "post_body": post.get("body", ""),
            "created_utc": created_utc,
            "subreddit": subreddit,
            "url": _normalize_url(permalink or f"/r/{subreddit}/comments/{post_id}/"),
            "type": "comment",
            "parent_post_id": post_id,
        })

    log.info(f"Browser comments for {post_id}: {len(comments)} items")
    return comments


def fetch_posts_from_subreddit_browser(page, subreddit: str, limit: int = 10) -> list[dict]:
    min_days = config["scraper"]["min_post_age_days"]
    max_days = config["scraper"]["max_post_age_days"]
    include_comments = config["scraper"].get("include_comments", False)
    sorts = PUBLIC_CFG.get("sorts", ["hot", "top", "new"])

    results = []
    seen = set()

    for sort in sorts:
        url = f"https://old.reddit.com/r/{subreddit}/{sort}/"
        if sort == "top":
            url += f"?t={PUBLIC_CFG.get('top_time_filter', 'week')}&limit={limit}"
        else:
            url += f"?limit={limit}"

        try:
            log.info(f"Browser fetch r/{subreddit}/{sort}")
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_PAGE_TIMEOUT_MS)
            page.wait_for_timeout(BROWSER_SETTLE_MS)
        except Exception as e:
            log.warning(f"Failed browser fetch {url}: {e}")
            continue

        title = ""
        try:
            title = page.title()
        except Exception:
            pass

        parsed = _extract_posts_from_page(page, subreddit)
        if not parsed:
            log.warning(f"Browser parsed 0 posts from {url}; title={title!r}")

        for item in parsed:
            post_id = item["id"]
            if post_id in seen or is_already_processed(post_id):
                continue
            seen.add(post_id)

            created_utc = time.time()
            if not _is_in_age_range(created_utc, min_days, max_days):
                continue

            post = {
                "id": post_id,
                "title": item["title"],
                "body": item["title"],
                "created_utc": created_utc,
                "subreddit": item.get("subreddit") or subreddit,
                "url": item["url"],
                "type": "post",
            }
            results.append(post)

            if include_comments:
                results.extend(fetch_comments_from_post_browser(page, subreddit, post))

    log.info(f"Browser r/{subreddit}: {len(results)} items")
    return results


def scrape_subreddits_browser(stop_callback=None, progress_callback=None) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed. Run: pip install -r requirements.txt") from e

    primary_subreddits = get_crawl_subreddits(config)
    total_limit = config["scraper"].get("max_items_per_day", 10)
    per_subreddit = max(1, total_limit // max(1, len(primary_subreddits)))
    per_subreddit = max(per_subreddit, int(PUBLIC_CFG.get("min_posts_per_subreddit", 2)))

    all_items = []
    log.info(f"Scraping {len(primary_subreddits)} subreddits via isolated browser...")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            raise RuntimeError("Could not launch Playwright Chromium. Run: playwright install chromium") from e

        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(BROWSER_PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(BROWSER_PAGE_TIMEOUT_MS)

        try:
            for index, sub in enumerate(primary_subreddits, start=1):
                if stop_callback and stop_callback():
                    log.info("Browser crawl stop requested.")
                    break
                if progress_callback:
                    progress_callback("crawl", index, len(primary_subreddits))

                items = fetch_posts_from_subreddit_browser(page, sub, limit=per_subreddit)
                for item in items:
                    insert_post(item, community_type="primary")
                all_items.extend(items)
        finally:
            try:
                log.info("Closing browser context...")
                context.close()
                log.info("Closing browser...")
                browser.close()
            except Exception as e:
                log.warning(f"Failed to close Playwright browser cleanly: {e}")

    log.info(f"Total browser items scraped: {len(all_items)}")
    return all_items
