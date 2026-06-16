from datetime import UTC, datetime


def get_crawl_subreddits(config: dict) -> list[str]:
    """Return core subreddits plus a small rotating sample of broad communities."""
    subreddit_cfg = config.get("subreddits", {})
    primary = list(subreddit_cfg.get("primary", []))
    sampled = list(subreddit_cfg.get("sampled", []))
    sampled_per_run = max(0, int(subreddit_cfg.get("sampled_per_run", 0)))

    if not sampled or sampled_per_run <= 0:
        return primary

    sample_size = min(sampled_per_run, len(sampled))
    now = datetime.now(UTC)
    offset = (now.toordinal() * 24 + now.hour) % len(sampled)
    selected = [sampled[(offset + index) % len(sampled)] for index in range(sample_size)]
    return primary + selected
