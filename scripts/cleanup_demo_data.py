# scripts/cleanup_demo_data.py

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config

DEMO_IDS = ["test_pain_1", "test_pain_2"]

config = get_config()
conn = sqlite3.connect(config["database"]["path"])
try:
    placeholders = ",".join("?" for _ in DEMO_IDS)
    deleted_posts = conn.execute(f"DELETE FROM posts WHERE id IN ({placeholders})", DEMO_IDS).rowcount
    deleted_history = conn.execute(f"DELETE FROM history WHERE id IN ({placeholders})", DEMO_IDS).rowcount
    conn.commit()
    print(f"deleted posts={deleted_posts}, history={deleted_history}")
finally:
    conn.close()
