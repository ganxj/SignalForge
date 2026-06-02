"""Shared persisted task state for UI-triggered and scheduled jobs."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path


TASK_STATUS_PATH = Path("data/task_status.json")

DEFAULT_TASK_STATE = {
    "crawl": {
        "running": False,
        "last_started_at": None,
        "last_finished_at": None,
        "last_message": "",
        "last_error": "",
        "stage": "",
        "current": 0,
        "total": 0,
        "heartbeat_at": None,
        "stop_requested": False,
        "pid": None,
        "pending": False,
    },
    "analysis": {
        "running": False,
        "last_started_at": None,
        "last_finished_at": None,
        "last_message": "",
        "last_error": "",
        "stage": "",
        "current": 0,
        "total": 0,
        "heartbeat_at": None,
        "stop_requested": False,
        "pid": None,
        "pending": False,
    },
}


def utc_now_label() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_task_state_from_disk() -> dict:
    if not TASK_STATUS_PATH.exists():
        return json.loads(json.dumps(DEFAULT_TASK_STATE))

    try:
        with TASK_STATUS_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT_TASK_STATE))

    merged = json.loads(json.dumps(DEFAULT_TASK_STATE))
    for task, task_state in state.items():
        if task in merged and isinstance(task_state, dict):
            merged[task].update(task_state)
    return merged


def save_task_state_to_disk(state: dict):
    TASK_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = TASK_STATUS_PATH.with_suffix(f".{os.getpid()}.json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, TASK_STATUS_PATH)


def set_task_state(task: str, **updates):
    state = load_task_state_from_disk()
    state[task].update(updates)
    save_task_state_to_disk(state)


def is_task_stop_requested(task: str) -> bool:
    return bool(load_task_state_from_disk().get(task, {}).get("stop_requested"))


def consume_pending(task: str) -> bool:
    state = load_task_state_from_disk()
    pending = bool(state.get(task, {}).get("pending"))
    if pending:
        state[task]["pending"] = False
        save_task_state_to_disk(state)
    return pending
