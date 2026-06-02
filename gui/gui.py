import json
import importlib
import inspect
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is importable when running via `streamlit run gui/gui.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config
from db.cleaner import clean_old_entries
from db.schema import create_tables
from scheduler.task_state import (
    DEFAULT_TASK_STATE,
    TASK_STATUS_PATH,
    load_task_state_from_disk,
    save_task_state_to_disk,
    set_task_state,
    utc_now_label,
)
from utils.helpers import ensure_directory_exists, sanitize_text


st.set_page_config(
    page_title="Reddit Posts Insights Viewer",
    page_icon="R",
    layout="wide",
)

CRAWL_LOCK = threading.Lock()
ANALYZE_LOCK = threading.Lock()
TASK_STATE_LOCK = threading.Lock()
INSIGHT_CATEGORIES = [
    "unclassified",
    "valuable",
    "maybe",
    "not_valuable",
    "too_hard",
    "existing_solution",
    "lesson_learned",
    "off_target",
]
TRANSLATIONS = {
    "en": {
        "language": "Language",
        "english": "English",
        "chinese": "Chinese",
        "app_title": "Reddit Posts Insights Viewer",
        "status_caption": "Reddit mode: {reddit_mode} | AI provider: {provider} | Targets: {targets}",
        "scheduled_start_failed": "Scheduled crawl could not start: {error}",
        "scraped_posts": "Scraped Posts",
        "ai_insights": "AI Insights",
        "crawl_reddit": "Crawl Reddit",
        "scheduled_enabled": "Scheduled crawl is enabled at minute 0 of every hour while this app is running.",
        "scheduled_disabled": "Scheduled crawl is disabled. Enable it in config/config.yaml -> reddit.scheduled_crawl_enabled.",
        "crawling": "Crawling Reddit via {mode}...",
        "crawl_completed": "Crawl completed via {mode}. Scraped {count} items.",
        "crawl_failed": "Crawl failed: {error}",
        "crawl_started": "Crawl started in the background.",
        "crawl_running": "Crawl is running in the background.",
        "last_result": "Last result",
        "last_error": "Last error",
        "started_at": "Started at",
        "finished_at": "Finished at",
        "progress": "Progress",
        "filtering_stage": "Filtering",
        "insight_stage": "Generating insights",
        "completed_stage": "Completed",
        "no_progress_data": "No progress data yet. This may be an older task started before progress tracking was added.",
        "reset_stale_task": "Reset stale task status",
        "stale_task_reset": "Stale task status has been reset.",
        "stop_task": "Stop current task",
        "stop_requested": "Stop requested. The task will stop after the current request/item finishes.",
        "task_pending": "One follow-up run is queued.",
        "task_running_blocked": "This task is already running. Wait for it to finish before starting it again.",
        "no_scraped": "No scraped posts found. Click Crawl Reddit to fetch data.",
        "loaded_scraped": "Loaded {count} recently scraped posts/comments.",
        "total_scraped": "Total scraped",
        "processed_count": "Processed",
        "insight_count": "With insights",
        "unprocessed_count": "Pending",
        "status_filter": "Processing status",
        "status_pending": "Pending",
        "status_processed": "Processed",
        "status_with_insight": "With insights",
        "status_filtered_out": "Filtered out",
        "status_all": "All",
        "filter_raw_subreddit": "Filter raw posts by subreddit",
        "filter_type": "Filter by type",
        "showing_scraped": "Showing {shown} of {total} scraped items",
        "type": "Type",
        "subreddit": "Subreddit",
        "insight": "Insight",
        "done": "Done",
        "pending": "Pending",
        "relevance": "Relevance",
        "pain": "Pain",
        "emotion": "Emotion",
        "tech_depth": "Tech Depth",
        "roi": "ROI",
        "tech": "Tech",
        "suggested_solution": "Suggested Solution",
        "details": "Details",
        "justification": "Justification",
        "affected_audience": "Affected Audience",
        "business_type": "Business Type",
        "existing_alternatives": "Existing Alternatives",
        "build_complexity": "Build Complexity",
        "technical_moat": "Technical Moat",
        "business_model": "Business Model",
        "analysis_limit": "Analysis limit",
        "analyzable_count": "Analyzable now",
        "score_threshold": "Score threshold",
        "analysis_caption": "Analyze uses the local OpenAI-compatible chat completions path and processes existing scraped items.",
        "analyze_existing": "Analyze Existing Posts",
        "analyzing": "Analyzing existing scraped posts. This can take a while...",
        "analysis_completed": "Analysis completed. Refreshing insights.",
        "analysis_failed": "Analysis failed: {error}",
        "analysis_started": "Analysis started in the background.",
        "analysis_running": "Analysis is running in the background.",
        "loading_insights": "Loading posts and insights...",
        "load_error": "Error loading data: {error}",
        "no_insights": "No posts with processed insights found. Run analysis before using this tab.",
        "loaded_insights": "Loaded {count} posts with insights (provider: {provider})",
        "filters_sorting": "Filters & Sorting",
        "score_filters": "Score Filters",
        "roi_range": "ROI Range",
        "relevance_range": "Relevance Score Range",
        "pain_range": "Pain Score Range",
        "emotion_range": "Emotion Score Range",
        "tech_depth_range": "Technical Depth Range",
        "same_value": "**{label}**: {value:.2f} (all posts have same value)",
        "subreddits": "Subreddits",
        "sorting": "Sorting",
        "sort_by": "Sort by",
        "sort_order": "Sort order",
        "descending": "Descending",
        "ascending": "Ascending",
        "showing_posts": "Showing {shown} of {total} posts",
        "page": "Page",
        "summary_stats": "Summary Stats",
        "total_posts": "Total Posts",
        "avg_relevance": "Avg Relevance",
        "avg_pain": "Avg Pain Score",
        "avg_emotion": "Avg Emotion Score",
        "avg_tech_depth": "Avg Tech Depth",
        "post_type": "Post",
        "comment_type": "Comment",
        "sort_relevance": "Relevance score",
        "sort_pain": "Pain score",
        "sort_emotion": "Emotion score",
        "sort_tech_depth": "Technical depth score",
        "sort_roi": "ROI",
        "sort_created": "Created time",
        "manual_category": "Manual category",
        "category_filter": "Manual category",
        "category_unclassified": "Unclassified",
        "category_valuable": "Worth trying",
        "category_maybe": "Maybe",
        "category_not_valuable": "Not valuable",
        "category_too_hard": "Too hard",
        "category_existing_solution": "Existing solution",
        "category_lesson_learned": "Lesson learned",
        "category_off_target": "Off target",
    },
    "zh": {
        "language": "语言",
        "english": "英文",
        "chinese": "中文",
        "app_title": "Reddit 帖子洞察看板",
        "status_caption": "Reddit 模式：{reddit_mode} | AI 服务：{provider} | 目标：{targets}",
        "scheduled_start_failed": "定时抓取启动失败：{error}",
        "scraped_posts": "已抓取内容",
        "ai_insights": "AI 洞察",
        "crawl_reddit": "抓取 Reddit",
        "scheduled_enabled": "页面运行期间，每小时 0 分自动抓取一次。",
        "scheduled_disabled": "定时抓取已关闭，可在 config/config.yaml -> reddit.scheduled_crawl_enabled 开启。",
        "crawling": "正在通过 {mode} 抓取 Reddit...",
        "crawl_completed": "抓取完成，模式：{mode}，本次返回 {count} 条。",
        "crawl_failed": "抓取失败：{error}",
        "crawl_started": "抓取任务已在后台启动。",
        "crawl_running": "抓取任务正在后台运行。",
        "last_result": "上次结果",
        "last_error": "上次错误",
        "started_at": "开始时间",
        "finished_at": "完成时间",
        "progress": "进度",
        "filtering_stage": "过滤评分",
        "insight_stage": "生成洞察",
        "completed_stage": "已完成",
        "no_progress_data": "暂无进度数据。这个任务可能是在进度跟踪功能加入之前启动的旧任务。",
        "reset_stale_task": "重置卡住的任务状态",
        "stale_task_reset": "已重置卡住的任务状态。",
        "stop_task": "停止当前任务",
        "stop_requested": "已请求停止，当前请求/条目处理完成后会停止。",
        "task_pending": "已排队 1 次后续运行。",
        "task_running_blocked": "该任务正在执行，请等待完成后再重新启动。",
        "no_scraped": "暂无抓取内容。点击“抓取 Reddit”获取数据。",
        "loaded_scraped": "已加载最近抓取的 {count} 条帖子/评论。",
        "total_scraped": "已抓取总数",
        "processed_count": "已处理",
        "insight_count": "已生成洞察",
        "unprocessed_count": "待处理",
        "status_filter": "处理状态",
        "status_pending": "待处理",
        "status_processed": "已处理",
        "status_with_insight": "已生成洞察",
        "status_filtered_out": "过滤淘汰",
        "status_all": "全部",
        "filter_raw_subreddit": "按 subreddit 筛选原始内容",
        "filter_type": "按类型筛选",
        "showing_scraped": "正在显示 {shown} / {total} 条抓取内容",
        "type": "类型",
        "subreddit": "Subreddit",
        "insight": "洞察",
        "done": "完成",
        "pending": "待处理",
        "relevance": "相关性",
        "pain": "痛点",
        "emotion": "情绪",
        "tech_depth": "技术深度",
        "roi": "ROI",
        "tech": "技术",
        "suggested_solution": "建议方案",
        "details": "详情",
        "justification": "理由",
        "affected_audience": "受影响人群",
        "business_type": "业务类型",
        "existing_alternatives": "现有替代方案",
        "build_complexity": "构建复杂度",
        "technical_moat": "技术壁垒",
        "business_model": "商业模式",
        "analysis_limit": "分析数量",
        "analyzable_count": "当前可分析",
        "score_threshold": "分数阈值",
        "analysis_caption": "分析会调用本地或 OpenAI 兼容 chat completions 接口，处理已抓取但尚未分析的数据。",
        "analyze_existing": "分析现有内容",
        "analyzing": "正在分析已有抓取内容，可能需要一段时间...",
        "analysis_completed": "分析完成，正在刷新洞察。",
        "analysis_failed": "分析失败：{error}",
        "analysis_started": "分析任务已在后台启动。",
        "analysis_running": "分析任务正在后台运行。",
        "loading_insights": "正在加载帖子和洞察...",
        "load_error": "加载数据失败：{error}",
        "no_insights": "暂无已完成 AI 洞察的数据。请先点击分析按钮。",
        "loaded_insights": "已加载 {count} 条 AI 洞察（provider: {provider}）。",
        "filters_sorting": "筛选与排序",
        "score_filters": "分数筛选",
        "roi_range": "ROI 范围",
        "relevance_range": "相关性范围",
        "pain_range": "痛点分范围",
        "emotion_range": "情绪分范围",
        "tech_depth_range": "技术深度范围",
        "same_value": "**{label}**：{value:.2f}（所有内容数值相同）",
        "subreddits": "Subreddit",
        "sorting": "排序",
        "sort_by": "排序字段",
        "sort_order": "排序方向",
        "descending": "降序",
        "ascending": "升序",
        "showing_posts": "正在显示 {shown} / {total} 条帖子",
        "page": "页码",
        "summary_stats": "汇总统计",
        "total_posts": "总数",
        "avg_relevance": "平均相关性",
        "avg_pain": "平均痛点分",
        "avg_emotion": "平均情绪分",
        "avg_tech_depth": "平均技术深度",
        "post_type": "帖子",
        "comment_type": "评论",
        "sort_relevance": "相关性分数",
        "sort_pain": "痛点分",
        "sort_emotion": "情绪分",
        "sort_tech_depth": "技术深度分",
        "sort_roi": "ROI",
        "sort_created": "创建时间",
        "manual_category": "人工分类",
        "category_filter": "人工分类",
        "category_unclassified": "未分类",
        "category_valuable": "可以尝试",
        "category_maybe": "暂不确定",
        "category_not_valuable": "没有价值",
        "category_too_hard": "难度太大",
        "category_existing_solution": "已有成熟方案",
        "category_lesson_learned": "经验教训",
        "category_off_target": "非目标方向",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    text = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def get_default_language(cfg: dict) -> str:
    lang = cfg.get("ui", {}).get("default_language", "zh")
    return lang if lang in TRANSLATIONS else "zh"


def display_post_type(lang: str, post_type: str) -> str:
    if post_type == "comment":
        return t(lang, "comment_type")
    return t(lang, "post_type")


def display_sort_field(lang: str, field: str) -> str:
    labels = {
        "relevance_score": t(lang, "sort_relevance"),
        "pain_score": t(lang, "sort_pain"),
        "emotion_score": t(lang, "sort_emotion"),
        "technical_depth_score": t(lang, "sort_tech_depth"),
        "roi_weight": t(lang, "sort_roi"),
        "created_utc": t(lang, "sort_created"),
    }
    return labels.get(field, field)


def display_insight_category(lang: str, category: str) -> str:
    return t(lang, f"category_{category}")


def ensure_manual_category_column(db_path: str):
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN manual_category TEXT DEFAULT 'unclassified'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def update_manual_category(db_path: str, post_id: str, category: str):
    if category not in INSIGHT_CATEGORIES:
        return
    ensure_manual_category_column(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE posts SET manual_category = ? WHERE id = ?",
            (category, post_id),
        )
        conn.commit()
    finally:
        conn.close()


def display_processing_status(lang: str, status: str) -> str:
    return {
        "pending": t(lang, "status_pending"),
        "processed": t(lang, "status_processed"),
        "with_insight": t(lang, "status_with_insight"),
        "filtered_out": t(lang, "status_filtered_out"),
        "all": t(lang, "status_all"),
    }.get(status, status)


def is_pid_running(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def is_heartbeat_stale(heartbeat: str | None, max_age_seconds: int = 300) -> bool:
    if not heartbeat:
        return True
    try:
        heartbeat_at = datetime.strptime(heartbeat, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except Exception:
        return True
    return (datetime.now(UTC) - heartbeat_at).total_seconds() > max_age_seconds


def get_task_state(task: str) -> dict:
    with TASK_STATE_LOCK:
        state = load_task_state_from_disk()
        task_state = state[task]
        pid = task_state.get("pid")
        running_without_live_pid = task_state.get("running") and (
            (pid and not is_pid_running(pid))
            or (not pid and is_heartbeat_stale(task_state.get("heartbeat_at")))
        )
        if running_without_live_pid:
            task_state.update(
                running=False,
                last_finished_at=utc_now_label(),
                last_error=task_state.get("last_error") or "Task process exited without updating status.",
                pid=None,
            )
            save_task_state_to_disk(state)
        return dict(task_state)


def reset_task_state(task: str):
    with TASK_STATE_LOCK:
        state = load_task_state_from_disk()
        state[task] = json.loads(json.dumps(DEFAULT_TASK_STATE[task]))
        state[task]["last_message"] = "Task status reset."
        state[task]["last_finished_at"] = utc_now_label()
        save_task_state_to_disk(state)


def request_task_stop(task: str):
    set_task_state(task, stop_requested=True, pending=False, last_error="")


def is_task_stop_requested(task: str) -> bool:
    return bool(get_task_state(task).get("stop_requested"))


def background_crawl(cfg: dict):
    now = utc_now_label()
    set_task_state(
        "crawl",
        running=True,
        last_started_at=now,
        last_finished_at=None,
        heartbeat_at=now,
        last_error="",
        last_message="",
        stage="",
        current=0,
        total=0,
        stop_requested=False,
    )
    try:
        mode_used, items = run_configured_crawl(
            cfg,
            stop_callback=lambda: is_task_stop_requested("crawl"),
            progress_callback=lambda stage, current, total: set_task_state(
                "crawl", stage=stage, current=current, total=total, heartbeat_at=utc_now_label()
            ),
        )
        st.cache_data.clear()
        message = f"Crawl completed via {mode_used}. Scraped {len(items)} items."
        if is_task_stop_requested("crawl"):
            message = f"Crawl stopped via {mode_used}. Scraped {len(items)} items."
        set_task_state(
            "crawl",
            running=False,
            last_finished_at=utc_now_label(),
            last_message=message,
            heartbeat_at=utc_now_label(),
            stop_requested=False,
        )
    except Exception as e:
        st.cache_data.clear()
        set_task_state(
            "crawl",
            running=False,
            last_finished_at=utc_now_label(),
            last_error=str(e),
            heartbeat_at=utc_now_label(),
            stop_requested=False,
        )


def background_analysis(limit: int, threshold: float):
    now = utc_now_label()
    set_task_state(
        "analysis",
        running=True,
        last_started_at=now,
        heartbeat_at=now,
        last_error="",
        last_message="",
        stage="",
        current=0,
        total=0,
        stop_requested=False,
    )
    try:
        def progress_callback(stage: str, current: int, total: int):
            set_task_state("analysis", stage=stage, current=current, total=total, heartbeat_at=utc_now_label())

        run_local_analysis(
            limit=limit,
            threshold=threshold,
            progress_callback=progress_callback,
            stop_callback=lambda: is_task_stop_requested("analysis"),
        )
        st.cache_data.clear()
        message = "Analysis completed."
        if is_task_stop_requested("analysis"):
            message = "Analysis stopped."
        set_task_state(
            "analysis",
            running=False,
            last_finished_at=utc_now_label(),
            last_message=message,
            stage="completed",
            current=0,
            total=0,
            heartbeat_at=utc_now_label(),
            stop_requested=False,
        )
    except Exception as e:
        st.cache_data.clear()
        set_task_state(
            "analysis",
            running=False,
            last_finished_at=utc_now_label(),
            last_error=str(e),
            heartbeat_at=utc_now_label(),
            stop_requested=False,
        )


def start_background_task(task: str, target, *args) -> bool:
    state = get_task_state(task)
    if state["running"]:
        return False

    now = utc_now_label()
    set_task_state(
        task,
        running=True,
        last_started_at=now,
        last_finished_at=None,
        heartbeat_at=now,
        last_error="",
        last_message="",
        stage="",
        current=0,
        total=0,
        stop_requested=False,
    )
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return True


def start_crawl_process() -> bool:
    state = get_task_state("crawl")
    if state["running"]:
        return False

    now = utc_now_label()
    set_task_state(
        "crawl",
        running=True,
        last_started_at=now,
        last_finished_at=None,
        heartbeat_at=now,
        last_error="",
        last_message="",
        stage="",
        current=0,
        total=0,
        stop_requested=False,
        pid=None,
        pending=False,
    )

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "scheduler.crawl_worker"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    set_task_state("crawl", pid=process.pid, heartbeat_at=utc_now_label())
    return True


def queue_task_once(task: str):
    set_task_state(task, pending=True, last_message="One follow-up run is queued.")


def schedule_crawl_once() -> bool:
    if get_task_state("crawl")["running"]:
        queue_task_once("crawl")
        return False
    return start_crawl_process()


def start_analysis_process(limit: int | None, threshold: float, analyze_all: bool = False) -> bool:
    state = get_task_state("analysis")
    if state["running"]:
        return False

    now = utc_now_label()
    set_task_state(
        "analysis",
        running=True,
        last_started_at=now,
        last_finished_at=None,
        heartbeat_at=now,
        last_error="",
        last_message="",
        stage="",
        current=0,
        total=0,
        stop_requested=False,
        pid=None,
        pending=False,
    )

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = [sys.executable, "-m", "scheduler.analysis_worker", "--threshold", str(float(threshold))]
    if analyze_all:
        args.append("--all")
    elif limit is not None:
        args.extend(["--limit", str(int(limit))])

    process = subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    set_task_state("analysis", pid=process.pid, heartbeat_at=utc_now_label())
    return True


def schedule_analysis_once(cfg: dict) -> bool:
    if get_task_state("analysis")["running"]:
        queue_task_once("analysis")
        return False
    threshold = float(cfg.get("scoring", {}).get("analysis_threshold", 7.0))
    return start_analysis_process(None, threshold, analyze_all=True)


def render_task_status(task: str, lang: str, running_key: str):
    state = get_task_state(task)
    if state["running"]:
        st.info(t(lang, running_key))
        if state["last_started_at"]:
            st.caption(f"{t(lang, 'started_at')}: {state['last_started_at']}")
        if st.button(t(lang, "stop_task"), key=f"stop_{task}"):
            request_task_stop(task)
            st.warning(t(lang, "stop_requested"))
    total = int(state.get("total") or 0)
    current = int(state.get("current") or 0)
    if state["running"] and total > 0:
        progress_value = min(max(current / total, 0), 1)
        stage_label = {
            "filtering": t(lang, "filtering_stage"),
            "insight": t(lang, "insight_stage"),
            "completed": t(lang, "completed_stage"),
        }.get(state.get("stage"), state.get("stage") or "")
        st.progress(progress_value, text=f"{t(lang, 'progress')}: {stage_label} {current}/{total}")
    elif state["running"]:
        st.warning(t(lang, "no_progress_data"))
        if st.button(t(lang, "reset_stale_task"), key=f"reset_{task}"):
            reset_task_state(task)
            st.success(t(lang, "stale_task_reset"))
    if state["last_message"]:
        st.caption(f"{t(lang, 'last_result')}: {state['last_message']}")
    if state.get("pending"):
        st.caption(t(lang, "task_pending"))
    if state["last_finished_at"]:
        st.caption(f"{t(lang, 'finished_at')}: {state['last_finished_at']}")
    if state["last_error"]:
        st.error(f"{t(lang, 'last_error')}: {state['last_error']}")


def render_task_status_live(task: str, lang: str, running_key: str):
    render_task_status(task, lang, running_key)


if hasattr(st, "fragment"):
    render_task_status_live = st.fragment(run_every="2s")(render_task_status_live)




def get_reddit_mode(cfg: dict) -> str:
    return cfg.get("reddit", {}).get("mode", "public_json")


def get_scheduled_task_config(cfg: dict) -> tuple[bool, bool]:
    reddit_cfg = cfg.get("reddit", {})
    ai_cfg = cfg.get("ai", {})
    crawl_enabled = bool(reddit_cfg.get("scheduled_crawl_enabled", False))
    analysis_enabled = bool(ai_cfg.get("scheduled_analysis_enabled", True))
    return crawl_enabled, analysis_enabled


def run_configured_crawl(cfg: dict, stop_callback=None, progress_callback=None) -> tuple[str, list[dict]]:
    if not CRAWL_LOCK.acquire(blocking=False):
        raise RuntimeError("A crawl is already running.")

    try:
        return _run_configured_crawl_unlocked(
            cfg,
            stop_callback=stop_callback,
            progress_callback=progress_callback,
        )
    finally:
        CRAWL_LOCK.release()


def _run_configured_crawl_unlocked(cfg: dict, stop_callback=None, progress_callback=None) -> tuple[str, list[dict]]:
    mode = get_reddit_mode(cfg)

    ensure_directory_exists("data")
    ensure_directory_exists("logs")
    create_tables()
    clean_old_entries()

    if mode == "api":
        from reddit.scraper import scrape_subreddits

        return mode, scrape_subreddits()

    if mode == "public_json":
        from reddit.scraper_public import scrape_subreddits_public

        return mode, scrape_subreddits_public(stop_callback=stop_callback, progress_callback=progress_callback)

    if mode == "html":
        from reddit.scraper_web import scrape_subreddits_web

        return mode, scrape_subreddits_web(stop_callback=stop_callback, progress_callback=progress_callback)

    if mode == "browser":
        from reddit.scraper_browser import scrape_subreddits_browser

        return mode, scrape_subreddits_browser(stop_callback=stop_callback, progress_callback=progress_callback)

    raise ValueError("Invalid reddit.mode. Use 'public_json', 'html', 'browser', or 'api'.")


def scheduled_crawl_job():
    schedule_crawl_once()


def scheduled_analysis_job():
    schedule_analysis_once(get_config())


@st.cache_resource
def start_task_scheduler(crawl_enabled: bool, analysis_enabled: bool):
    if not crawl_enabled and not analysis_enabled:
        return None

    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    if crawl_enabled:
        scheduler.add_job(
            scheduled_crawl_job,
            trigger="cron",
            minute=0,
            id="scheduled_reddit_crawl",
            name="Scheduled Reddit Crawl",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    if analysis_enabled:
        scheduler.add_job(
            scheduled_analysis_job,
            trigger="cron",
            minute=30,
            id="scheduled_ai_analysis",
            name="Scheduled AI Analysis",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    return scheduler


def run_local_analysis(limit: int, threshold: float, progress_callback=None, stop_callback=None):
    if not ANALYZE_LOCK.acquire(blocking=False):
        raise RuntimeError("An analysis run is already running.")

    try:
        import analyze_local

        analyze_local = importlib.reload(analyze_local)
        analyze = analyze_local.analyze
        kwargs = {"limit": limit, "threshold": threshold}
        signature = inspect.signature(analyze)
        if "progress_callback" in signature.parameters:
            kwargs["progress_callback"] = progress_callback
        if "stop_callback" in signature.parameters:
            kwargs["stop_callback"] = stop_callback
        analyze(**kwargs)
    finally:
        ANALYZE_LOCK.release()


def _extract_json_from_text(text: str) -> str:
    if not text:
        return text

    stripped = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", stripped, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"[\{\[].*[\}\]]", stripped, re.DOTALL)
    if match:
        return match.group(0).strip()

    return stripped


def get_data_version(db_path: str, insights_dir: str) -> float:
    mtimes = []
    if os.path.exists(db_path):
        mtimes.append(os.path.getmtime(db_path))

    insights_path = Path(insights_dir)
    if insights_path.exists():
        mtimes.extend(f.stat().st_mtime for f in insights_path.glob("insight_result_*.jsonl"))

    return max(mtimes, default=0.0)


@st.cache_data
def load_recent_posts(
    db_path: str,
    data_version: float,
    selected_statuses: tuple[str, ...],
    selected_types: tuple[str, ...],
    selected_subreddits: tuple[str, ...],
    limit: int = 500,
) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()

    status_filter = ""
    params = []

    if "all" not in selected_statuses:
        status_conditions = []
        if "pending" in selected_statuses:
            status_conditions.append("(h.id IS NULL AND COALESCE(p.insight_processed, 0) = 0)")
        if "with_insight" in selected_statuses:
            status_conditions.append("COALESCE(p.insight_processed, 0) = 1")
        if "filtered_out" in selected_statuses:
            status_conditions.append("(h.id IS NOT NULL AND COALESCE(p.insight_processed, 0) = 0)")
        if "processed" in selected_statuses:
            status_conditions.append("h.id IS NOT NULL")
        if status_conditions:
            status_filter = "WHERE (" + " OR ".join(status_conditions) + ")"
        else:
            status_filter = "WHERE 1 = 0"

    filters = [status_filter] if status_filter else []
    if selected_types:
        filters.append(f"p.type IN ({','.join('?' for _ in selected_types)})")
        params.extend(selected_types)
    if selected_subreddits:
        filters.append(f"p.subreddit IN ({','.join('?' for _ in selected_subreddits)})")
        params.extend(selected_subreddits)

    where_clause = ""
    if filters:
        normalized = []
        for index, condition in enumerate(filters):
            if index == 0 and condition.startswith("WHERE "):
                normalized.append(condition[6:])
            else:
                normalized.append(condition)
        where_clause = "WHERE " + " AND ".join(normalized)

    conn = sqlite3.connect(db_path)
    query = f"""
    SELECT p.id, p.url, p.title, p.body, p.subreddit, p.type, p.parent_post_id, p.created_utc,
           p.processed_at, p.relevance_score, p.pain_score, p.emotion_score,
           COALESCE(p.technical_depth_score, 0) as technical_depth_score,
           COALESCE(p.insight_processed, 0) as insight_processed,
           CASE WHEN h.id IS NOT NULL THEN 1 ELSE 0 END AS is_processed,
           CASE
               WHEN COALESCE(p.insight_processed, 0) = 1 THEN 'with_insight'
               WHEN h.id IS NOT NULL THEN 'filtered_out'
               ELSE 'pending'
           END AS processing_status
    FROM posts p
    LEFT JOIN history h ON h.id = p.id
    {where_clause}
    ORDER BY p.created_utc DESC
    LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=tuple(params + [limit]))
    conn.close()

    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_utc"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M UTC")

    return df


@st.cache_data
def load_scraped_summary(db_path: str, data_version: float) -> dict:
    if not os.path.exists(db_path):
        return {"total": 0, "processed": 0, "with_insight": 0, "pending": 0}

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN h.id IS NOT NULL THEN 1 ELSE 0 END) AS processed,
            SUM(CASE WHEN COALESCE(p.insight_processed, 0) = 1 THEN 1 ELSE 0 END) AS with_insight
        FROM posts p
        LEFT JOIN history h ON h.id = p.id
        """
    ).fetchone()
    conn.close()

    total = int(row[0] or 0)
    processed = int(row[1] or 0)
    with_insight = int(row[2] or 0)
    return {
        "total": total,
        "processed": processed,
        "with_insight": with_insight,
        "pending": total - processed,
    }


@st.cache_data
def load_all_subreddits(db_path: str, data_version: float) -> list[str]:
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT subreddit FROM posts WHERE subreddit IS NOT NULL ORDER BY subreddit"
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


@st.cache_data
def load_analyzable_count(db_path: str, data_version: float) -> int:
    if not os.path.exists(db_path):
        return 0

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT title, body
        FROM posts
        WHERE id NOT IN (SELECT id FROM history)
          AND (insight_processed IS NULL OR insight_processed = 0)
        """
    ).fetchall()
    conn.close()
    return sum(1 for title, body in rows if sanitize_text(title) and sanitize_text(body))


@st.cache_data
def load_posts_with_insights(
    db_path: str,
    insights_dir: str,
    provider: str,
    data_version: float,
) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()

    ensure_manual_category_column(db_path)
    conn = sqlite3.connect(db_path)
    query = """
    SELECT id, url, title, body, relevance_score, pain_score, emotion_score,
           COALESCE(technical_depth_score, 0) as technical_depth_score,
           subreddit, created_utc, processed_at,
           COALESCE(manual_category, 'unclassified') as manual_category
    FROM posts
    WHERE insight_processed = 1
    """
    posts_df = pd.read_sql_query(query, conn)
    conn.close()

    insights_data = {}
    insights_path = Path(insights_dir)
    if insights_path.exists():
        for jsonl_file in insights_path.glob("insight_result_*.jsonl"):
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        custom_id = data.get("custom_id")
                        if not custom_id:
                            continue

                        if provider == "anthropic":
                            if data.get("result_type") != "succeeded":
                                continue
                            content = data.get("content", "")
                            insights_data[custom_id] = json.loads(_extract_json_from_text(content))
                        elif provider == "openai":
                            response = data.get("response", {})
                            body = response.get("body", {})
                            choices = body.get("choices", [])
                            if choices:
                                content = choices[0]["message"]["content"]
                                insights_data[custom_id] = json.loads(_extract_json_from_text(content))
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    posts_df["pain_point"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("pain_point", ""))
    posts_df["tags"] = posts_df["id"].map(lambda x: ", ".join(insights_data.get(x, {}).get("tags", [])))
    posts_df["roi_weight"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("roi_weight", 0))
    posts_df["justification"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("justification", ""))
    posts_df["product_opportunity"] = posts_df["id"].map(
        lambda x: insights_data.get(x, {}).get("product_opportunity", "")
    )
    posts_df["affected_audience"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("affected_audience", ""))
    posts_df["existing_alternatives"] = posts_df["id"].map(
        lambda x: insights_data.get(x, {}).get("existing_alternatives", "")
    )
    posts_df["build_complexity"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("build_complexity", ""))
    posts_df["technical_moat"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("technical_moat", ""))
    posts_df["business_model"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("business_model", ""))
    posts_df["business_type"] = posts_df["id"].map(lambda x: insights_data.get(x, {}).get("business_type", ""))

    return posts_df


def display_raw_post_card(post: pd.Series, lang: str):
    with st.container():
        st.markdown("---")
        col1, col2, col3, col4 = st.columns([2, 2, 2, 8])
        with col1:
            st.metric(t(lang, "type"), display_post_type(lang, post.get("type", "post")))
        with col2:
            st.metric(t(lang, "subreddit"), f"r/{post['subreddit']}")
        with col3:
            status = display_processing_status(lang, post.get("processing_status", "pending"))
            st.metric(t(lang, "insight"), status)
        with col4:
            st.markdown(f"[{post['title']}](<{post['url']}>)")
            if post.get("created_at"):
                st.caption(post["created_at"])

        body = post.get("body", "")
        if body:
            preview = body[:500] + "..." if len(body) > 500 else body
            st.markdown(preview)

        score_parts = []
        for label, key in [
            (t(lang, "relevance"), "relevance_score"),
            (t(lang, "pain"), "pain_score"),
            (t(lang, "emotion"), "emotion_score"),
            (t(lang, "tech_depth"), "technical_depth_score"),
        ]:
            value = post.get(key)
            if pd.notna(value):
                score_parts.append(f"**{label}:** {float(value):.2f}")
        if score_parts:
            st.caption(" | ".join(score_parts))


def display_insight_card(post: pd.Series, lang: str, db_path: str):
    with st.container():
        st.markdown("---")
        col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 1, 1, 1, 9])

        with col1:
            st.metric(t(lang, "roi"), f"{post['roi_weight']}")
        with col2:
            st.metric(t(lang, "relevance"), f"{post['relevance_score']:.2f}")
        with col3:
            st.metric(t(lang, "pain"), f"{post['pain_score']:.2f}")
        with col4:
            st.metric(t(lang, "emotion"), f"{post['emotion_score']:.2f}")
        with col5:
            st.metric(t(lang, "tech"), f"{post['technical_depth_score']:.1f}")
        with col6:
            st.info(post["pain_point"])

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown(f"[{post['title']}](<{post['url']}>)")
    with col2:
        tags_list = [tag.strip() for tag in post["tags"].split(",") if tag.strip()]
        tags_html = "".join(
            f"<span style='background-color: #276EF1; color: white; padding: 2px 6px; "
            f"border-radius: 8px; font-size: 11px; margin-right: 4px; "
            f"display: inline-block; margin-bottom: 2px;'>{tag}</span>"
            for tag in tags_list
        )
        st.markdown(tags_html, unsafe_allow_html=True)

    current_category = post.get("manual_category") or "unclassified"
    if current_category not in INSIGHT_CATEGORIES:
        current_category = "unclassified"
    selected_category = st.selectbox(
        t(lang, "manual_category"),
        options=INSIGHT_CATEGORIES,
        index=INSIGHT_CATEGORIES.index(current_category),
        format_func=lambda item: display_insight_category(lang, item),
        key=f"manual_category_{post['id']}",
    )
    if selected_category != current_category:
        update_manual_category(db_path, post["id"], selected_category)
        st.cache_data.clear()
        st.rerun()

    if post.get("product_opportunity"):
        st.success(f"{t(lang, 'suggested_solution')}: {post['product_opportunity']}")

    with st.expander(t(lang, "details")):
        body_text = post["body"][:500] + "..." if len(post["body"]) > 500 else post["body"]
        st.markdown(body_text)

        if post["justification"]:
            st.markdown(f"**{t(lang, 'justification')}:**")
            st.markdown(post["justification"])

        for label, key in [
            (t(lang, "affected_audience"), "affected_audience"),
            (t(lang, "business_type"), "business_type"),
            (t(lang, "existing_alternatives"), "existing_alternatives"),
            (t(lang, "build_complexity"), "build_complexity"),
            (t(lang, "technical_moat"), "technical_moat"),
            (t(lang, "business_model"), "business_model"),
        ]:
            if post.get(key):
                st.markdown(f"**{label}:** {post[key]}")


def render_raw_posts_tab(cfg: dict, db_path: str, data_version: float, lang: str):
    reddit_mode = get_reddit_mode(cfg)
    scheduled_enabled, _ = get_scheduled_task_config(cfg)

    col1, col2 = st.columns([1, 3])
    with col1:
        crawl_clicked = st.button(
            t(lang, "crawl_reddit"),
            type="primary",
            use_container_width=True,
            disabled=get_task_state("crawl")["running"],
        )
    with col2:
        if scheduled_enabled:
            st.caption(t(lang, "scheduled_enabled", minutes=60))
        else:
            st.caption(t(lang, "scheduled_disabled"))

    if crawl_clicked:
        started = start_crawl_process()
        if started:
            st.cache_data.clear()
            st.success(t(lang, "crawl_started"))
        else:
            st.warning(t(lang, "task_running_blocked"))

    render_task_status_live("crawl", lang, "crawl_running")

    summary = load_scraped_summary(db_path, data_version)
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric(t(lang, "total_scraped"), summary["total"])
    metric_col2.metric(t(lang, "processed_count"), summary["processed"])
    metric_col3.metric(t(lang, "insight_count"), summary["with_insight"])
    metric_col4.metric(t(lang, "unprocessed_count"), summary["pending"])

    raw_subreddits = load_all_subreddits(db_path, data_version)
    selected_statuses = st.multiselect(
        t(lang, "status_filter"),
        options=["pending", "processed", "with_insight", "filtered_out", "all"],
        default=["pending"],
        format_func=lambda item: display_processing_status(lang, item),
        key="raw_statuses",
    )
    selected_raw_subreddits = st.multiselect(
        t(lang, "filter_raw_subreddit"),
        options=raw_subreddits,
        default=raw_subreddits,
        key="raw_subreddits",
    )
    selected_types = st.multiselect(
        t(lang, "filter_type"),
        options=["post", "comment"],
        default=["post", "comment"],
        format_func=lambda item: display_post_type(lang, item),
        key="raw_types",
    )

    raw_df = load_recent_posts(
        db_path,
        data_version,
        tuple(selected_statuses),
        tuple(selected_types),
        tuple(selected_raw_subreddits),
    )
    if raw_df.empty:
        st.warning(t(lang, "no_scraped"))
        return

    st.success(t(lang, "loaded_scraped", count=len(raw_df)))
    st.markdown(f"**{t(lang, 'showing_scraped', shown=len(raw_df), total=summary['total'])}**")
    for _, post in raw_df.iterrows():
        display_raw_post_card(post, lang)


def create_safe_slider(label: str, values: pd.Series, key: str, lang: str):
    min_val = float(values.min())
    max_val = float(values.max())

    if min_val == max_val:
        st.sidebar.write(t(lang, "same_value", label=label, value=min_val))
        return min_val, max_val

    return st.sidebar.slider(
        label,
        min_value=min_val,
        max_value=max_val,
        value=(min_val, max_val),
        step=0.1,
        key=key,
    )


def render_insights_tab(cfg: dict, db_path: str, insights_dir: str, provider: str, data_version: float, lang: str):
    default_limit = int(cfg.get("public_scraper", {}).get("max_items_to_analyze", 12))
    analyzable_count = load_analyzable_count(db_path, data_version)

    col0, col1, col2, col3 = st.columns([1, 1, 1, 2])
    with col0:
        st.metric(t(lang, "analyzable_count"), analyzable_count)
    with col1:
        safe_default_limit = min(default_limit, max(analyzable_count, 1))
        analysis_limit = st.number_input(
            t(lang, "analysis_limit"),
            min_value=1,
            max_value=500,
            value=safe_default_limit,
            step=1,
        )
    with col2:
        analysis_threshold = st.number_input(t(lang, "score_threshold"), min_value=0.0, max_value=10.0, value=7.0, step=0.1)
    with col3:
        st.caption(t(lang, "analysis_caption"))
        analyze_clicked = st.button(
            t(lang, "analyze_existing"),
            type="primary",
            use_container_width=True,
            disabled=get_task_state("analysis")["running"],
        )

    if analyze_clicked:
        started = start_analysis_process(int(analysis_limit), float(analysis_threshold))
        if started:
            st.cache_data.clear()
            st.success(t(lang, "analysis_started"))
        else:
            st.warning(t(lang, "task_running_blocked"))

    render_task_status_live("analysis", lang, "analysis_running")

    with st.spinner(t(lang, "loading_insights")):
        try:
            df = load_posts_with_insights(db_path, insights_dir, provider, data_version)
        except Exception as e:
            st.error(t(lang, "load_error", error=e))
            return

    if df.empty:
        st.warning(t(lang, "no_insights"))
        return

    st.success(t(lang, "loaded_insights", count=len(df), provider=provider))

    st.sidebar.header(t(lang, "filters_sorting"))
    st.sidebar.subheader(t(lang, "category_filter"))
    selected_category = st.sidebar.selectbox(
        t(lang, "category_filter"),
        options=INSIGHT_CATEGORIES,
        index=0,
        format_func=lambda item: display_insight_category(lang, item),
        key="insight_manual_category_filter",
    )
    df = df[df["manual_category"].fillna("unclassified") == selected_category]
    if df.empty:
        st.warning(t(lang, "no_insights"))
        return

    st.sidebar.subheader(t(lang, "score_filters"))

    roi_range = create_safe_slider(t(lang, "roi_range"), df["roi_weight"], "roi", lang)
    relevance_range = create_safe_slider(t(lang, "relevance_range"), df["relevance_score"], "relevance", lang)
    pain_range = create_safe_slider(t(lang, "pain_range"), df["pain_score"], "pain", lang)
    emotion_range = create_safe_slider(t(lang, "emotion_range"), df["emotion_score"], "emotion", lang)
    tech_depth_range = create_safe_slider(t(lang, "tech_depth_range"), df["technical_depth_score"], "tech_depth", lang)

    subreddits = df["subreddit"].unique().tolist()
    selected_subreddits = st.sidebar.multiselect(
        t(lang, "subreddits"),
        options=subreddits,
        default=subreddits,
    )

    st.sidebar.subheader(t(lang, "sorting"))
    sort_by = st.sidebar.selectbox(
        t(lang, "sort_by"),
        options=[
            "relevance_score",
            "pain_score",
            "emotion_score",
            "technical_depth_score",
            "roi_weight",
            "created_utc",
        ],
        format_func=lambda item: display_sort_field(lang, item),
        index=0,
    )
    sort_order = st.sidebar.radio(
        t(lang, "sort_order"),
        options=[t(lang, "descending"), t(lang, "ascending")],
        index=0,
    )

    filtered_df = df[
        (df["roi_weight"] >= roi_range[0])
        & (df["roi_weight"] <= roi_range[1])
        & (df["relevance_score"] >= relevance_range[0])
        & (df["relevance_score"] <= relevance_range[1])
        & (df["pain_score"] >= pain_range[0])
        & (df["pain_score"] <= pain_range[1])
        & (df["emotion_score"] >= emotion_range[0])
        & (df["emotion_score"] <= emotion_range[1])
        & (df["technical_depth_score"] >= tech_depth_range[0])
        & (df["technical_depth_score"] <= tech_depth_range[1])
        & (df["subreddit"].isin(selected_subreddits))
    ]

    ascending = sort_order == t(lang, "ascending")
    filtered_df = filtered_df.sort_values(by=sort_by, ascending=ascending)

    st.markdown(f"**{t(lang, 'showing_posts', shown=len(filtered_df), total=len(df))}**")

    posts_per_page = 10
    total_pages = (len(filtered_df) + posts_per_page - 1) // posts_per_page
    if total_pages > 1:
        page = st.selectbox(t(lang, "page"), range(1, total_pages + 1), index=0)
        start_idx = (page - 1) * posts_per_page
        page_df = filtered_df.iloc[start_idx:start_idx + posts_per_page]
    else:
        page_df = filtered_df

    for _, post in page_df.iterrows():
        display_insight_card(post, lang, db_path)

    if len(filtered_df) > 0:
        st.sidebar.subheader(t(lang, "summary_stats"))
        st.sidebar.metric(t(lang, "total_posts"), len(filtered_df))
        st.sidebar.metric(t(lang, "avg_relevance"), f"{filtered_df['relevance_score'].mean():.2f}")
        st.sidebar.metric(t(lang, "avg_pain"), f"{filtered_df['pain_score'].mean():.2f}")
        st.sidebar.metric(t(lang, "avg_emotion"), f"{filtered_df['emotion_score'].mean():.2f}")
        st.sidebar.metric(t(lang, "avg_tech_depth"), f"{filtered_df['technical_depth_score'].mean():.2f}")


def main():
    cfg = get_config()
    provider = cfg["ai"]["provider"]
    reddit_mode = get_reddit_mode(cfg)
    scheduled_crawl_enabled, scheduled_analysis_enabled = get_scheduled_task_config(cfg)
    db_path = cfg["database"]["path"]
    insights_dir = cfg.get("paths", {}).get("batch_responses_dir", "data/batch_responses")
    primary_subreddits = cfg.get("subreddits", {}).get("primary", [])
    default_language = get_default_language(cfg)

    lang = st.sidebar.selectbox(
        t(default_language, "language"),
        options=["zh", "en"],
        format_func=lambda code: t(code, "chinese") if code == "zh" else t(code, "english"),
        index=0 if default_language == "zh" else 1,
    )

    try:
        start_task_scheduler(scheduled_crawl_enabled, scheduled_analysis_enabled)
    except Exception as e:
        st.warning(t(lang, "scheduled_start_failed", error=e))

    st.title(t(lang, "app_title"))
    st.caption(
        t(
            lang,
            "status_caption",
            reddit_mode=reddit_mode,
            provider=provider,
            targets=", ".join("r/" + s for s in primary_subreddits),
        )
    )

    data_version = get_data_version(db_path, insights_dir)
    raw_tab, insights_tab = st.tabs([t(lang, "scraped_posts"), t(lang, "ai_insights")])

    with raw_tab:
        render_raw_posts_tab(cfg, db_path, data_version, lang)

    with insights_tab:
        render_insights_tab(cfg, db_path, insights_dir, provider, data_version, lang)


if __name__ == "__main__":
    main()
