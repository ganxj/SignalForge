# db/schema.py

import sqlite3
import os
from config.config_loader import get_config
from utils.logger import setup_logger
from utils.helpers import ensure_directory_exists

log = setup_logger()
config = get_config()
DB_PATH = config["database"]["path"]

def create_tables():
    """Create the SQLite tables if they don't exist."""
    ensure_directory_exists(os.path.dirname(DB_PATH))
    log.info(f"Initializing database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY,
        url TEXT,
        title TEXT,
        body TEXT,
        subreddit TEXT,
        created_utc REAL,
        last_active REAL,
        processed_at TEXT,
        relevance_score REAL,
        emotion_score REAL,
        pain_score REAL,
        tags TEXT,
        roi_weight INTEGER,
        community_type TEXT,
        type TEXT,  -- 'post' or 'comment'
        post_body TEXT,  -- parent post body for comments
        parent_post_id TEXT,  -- links comments to their parent post (for dedup)
        implementability_score REAL,
        technical_depth_score REAL,
        insight_processed INTEGER DEFAULT 0,
        insight_processed_at TEXT,
        analysis_status TEXT DEFAULT 'pending',
        analysis_error TEXT,
        analysis_attempted_at TEXT,
        prefilter_pass INTEGER,
        prefilter_reason TEXT,
        prefilter_processed_at TEXT,
        filter_pass INTEGER,
        filter_processed_at TEXT,
        manual_category TEXT DEFAULT 'unclassified'
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY,
        processed_at TEXT
    );
    """)

    existing_columns = {row[1] for row in c.execute("PRAGMA table_info(posts)").fetchall()}
    migrations = [
        ("technical_depth_score", "REAL"),
        ("parent_post_id", "TEXT"),
        ("manual_category", "TEXT DEFAULT 'unclassified'"),
        ("analysis_status", "TEXT DEFAULT 'pending'"),
        ("analysis_error", "TEXT"),
        ("analysis_attempted_at", "TEXT"),
        ("prefilter_pass", "INTEGER"),
        ("prefilter_reason", "TEXT"),
        ("prefilter_processed_at", "TEXT"),
        ("filter_pass", "INTEGER"),
        ("filter_processed_at", "TEXT"),
    ]
    for column_name, column_sql in migrations:
        if column_name not in existing_columns:
            c.execute(f"ALTER TABLE posts ADD COLUMN {column_name} {column_sql}")
            log.info(f"Added {column_name} column to posts table")

    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_processed_at ON posts(processed_at);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_relevance ON posts(relevance_score);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_roi ON posts(roi_weight);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_manual_category ON posts(manual_category);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_analysis_status ON posts(analysis_status);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_prefilter_pass ON posts(prefilter_pass);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_filter_pass ON posts(filter_pass);")

    conn.commit()
    conn.close()
    log.info("Database tables created successfully")

if __name__ == "__main__":
    create_tables()
    print(f"Database initialized at {DB_PATH}")
