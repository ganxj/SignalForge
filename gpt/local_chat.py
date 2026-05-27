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


def chat_json(messages: list[dict[str, str]], model: str, max_tokens: int = 1000, temperature: float = 0) -> dict[str, Any]:
    """Call local model and return parsed JSON."""
    client = get_client()
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Some OpenAI-compatible servers support this; if not, retry without it.
    try:
        resp = client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        log.warning(f"Chat completion with response_format failed, retrying without it: {e}")
        resp = client.chat.completions.create(**kwargs)

    content = resp.choices[0].message.content or ""
    return extract_json(content)
