"""Local OpenAI-compatible chat completions client.

This avoids OpenAI Batch API and talks directly to an OpenAI-compatible
/v1/chat/completions endpoint, e.g. http://127.0.0.1:8000/v1.
"""

import json
import re
from typing import Any

from openai import OpenAI

from config.config_loader import get_config
from utils.logger import setup_logger

log = setup_logger()
config = get_config()


def get_client() -> OpenAI:
    openai_cfg = config["ai"]["openai"]
    return OpenAI(
        api_key=openai_cfg.get("api_key") or "EMPTY",
        base_url=openai_cfg.get("base_url") or None,
    )


def extract_json(text: str) -> dict[str, Any]:
    """Parse JSON from plain text or markdown fenced model output."""
    if not text:
        raise ValueError("empty model response")

    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    else:
        obj = re.search(r"\{.*\}", raw, re.DOTALL)
        if obj:
            raw = obj.group(0).strip()

    return json.loads(raw)


def chat_text(messages: list[dict[str, str]], model: str, max_tokens: int = 1000, temperature: float = 0) -> str:
    """Call local model and return raw text content."""
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or ""
    if not content.strip():
        raise ValueError("empty model response")
    return content.strip()


def _normalize_key(text: str) -> str:
    text = re.sub(r"^\s*\d+[.)]\s*", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _markdown_key_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip().strip("*`") for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and not all(set(cell) <= {"-", ":"} for cell in cells):
                key = _normalize_key(cells[0])
                if key and key not in {"field", "metric", "score", "name"}:
                    values[key] = cells[1].strip()
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"^\*\*(.*?)\*\*", r"\1", line)
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = _normalize_key(key)
        if key:
            values[key] = value.strip().strip("`")
    return values


def _to_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return default
    return max(0.0, min(10.0, float(match.group(0))))


def _to_int(value: str | None, default: int = 1, min_value: int = 1, max_value: int = 5) -> int:
    if value is None:
        return default
    match = re.search(r"-?\d+", str(value))
    if not match:
        return default
    return max(min_value, min(max_value, int(match.group(0))))


def _section(text: str, names: list[str]) -> str:
    wanted = {_normalize_key(name) for name in names}
    current = None
    lines: list[str] = []
    for raw_line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", raw_line)
        if heading:
            current = _normalize_key(heading.group(1))
            continue
        if current in wanted:
            lines.append(raw_line.rstrip())
    return "\n".join(lines).strip()


def _field_or_section(values: dict[str, str], text: str, key: str, headings: list[str]) -> str:
    return values.get(key, "").strip() or _section(text, headings)


def _parse_tags(value: str, section: str) -> list[str]:
    raw = section or value
    tags = []
    for part in re.split(r"[\n,]", raw):
        part = re.sub(r"^[-*]\s+", "", part.strip())
        part = part.strip("`# ")
        if part:
            tags.append(part)
    return tags[:3]


def parse_filter_markdown(text: str) -> dict[str, Any]:
    """Parse the Markdown filter protocol into the score dict used by the app."""
    values = _markdown_key_values(text)
    required = {
        "relevance_score",
        "emotional_intensity",
        "pain_point_clarity",
        "implementability_score",
        "technical_depth_score",
    }
    missing = sorted(required - set(values))
    if missing:
        raise ValueError(f"missing Markdown filter fields: {', '.join(missing)}")
    return {
        "relevance_score": _to_float(values.get("relevance_score")),
        "emotional_intensity": _to_float(values.get("emotional_intensity")),
        "pain_point_clarity": _to_float(values.get("pain_point_clarity")),
        "implementability_score": _to_float(values.get("implementability_score"), default=5.0),
        "technical_depth_score": _to_float(values.get("technical_depth_score"), default=5.0),
        "summary": _field_or_section(values, text, "summary", ["Summary"]),
    }


def parse_prefilter_markdown(text: str) -> dict[str, Any]:
    """Parse the short yes/no prefilter gate."""
    values = _markdown_key_values(text)
    decision = (values.get("decision") or values.get("pass") or values.get("keep") or "").strip().lower()
    if not decision:
        raise ValueError("missing Markdown prefilter field: decision")
    accepted = decision in {"yes", "y", "true", "pass", "keep", "continue"}
    rejected = decision in {"no", "n", "false", "reject", "skip"}
    if not accepted and not rejected:
        raise ValueError(f"invalid Markdown prefilter decision: {decision}")
    return {
        "decision": "yes" if accepted else "no",
        "pass": accepted,
        "reason": values.get("reason", "").strip(),
    }


def parse_insight_markdown(text: str) -> dict[str, Any]:
    """Parse the Markdown insight protocol into the insight dict used by the app."""
    values = _markdown_key_values(text)
    tags_section = _section(text, ["Tags", "Market tags"])
    insight = {
        "pain_point": _field_or_section(values, text, "pain_point", ["Pain point", "Core pain point"]),
        "affected_audience": _field_or_section(values, text, "affected_audience", ["Affected audience"]),
        "business_type": _field_or_section(values, text, "business_type", ["Business type"]),
        "existing_alternatives": _field_or_section(values, text, "existing_alternatives", ["Existing alternatives"]),
        "product_opportunity": _field_or_section(values, text, "product_opportunity", ["Product opportunity", "MVP product opportunity"]),
        "build_complexity": _field_or_section(values, text, "build_complexity", ["Build complexity"]),
        "technical_moat": _field_or_section(values, text, "technical_moat", ["Technical moat", "Technical moat analysis"]),
        "business_model": _field_or_section(values, text, "business_model", ["Business model"]),
        "tags": _parse_tags(values.get("tags", ""), tags_section),
        "roi_weight": _to_int(values.get("roi_weight") or _section(text, ["ROI weight", "Viability assessment"])),
        "justification": _field_or_section(values, text, "justification", ["Justification"]),
    }
    required = ["pain_point", "product_opportunity", "roi_weight", "justification"]
    missing = [key for key in required if not insight.get(key)]
    if missing:
        raise ValueError(f"missing Markdown insight fields: {', '.join(missing)}")
    return insight


def chat_markdown(
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int = 1000,
    temperature: float = 0,
    parser=parse_filter_markdown,
) -> dict[str, Any]:
    """Call local model with Markdown output and parse the agreed Markdown fields."""
    content = chat_text(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    try:
        return parser(content)
    except Exception as markdown_error:
        # Compatibility fallback for older prompts or models that still return JSON.
        try:
            return extract_json(content)
        except Exception:
            raise markdown_error


def chat_json(messages: list[dict[str, str]], model: str, max_tokens: int = 1000, temperature: float = 0) -> dict[str, Any]:
    """Backward-compatible JSON helper for legacy batch/local paths."""
    content = chat_text(messages, model=model, max_tokens=max_tokens, temperature=temperature)
    return extract_json(content)
