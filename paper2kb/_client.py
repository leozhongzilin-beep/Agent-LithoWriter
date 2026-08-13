"""Environment plumbing for the LLM client (same convention as write_agent)."""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise OSError(
            "DEEPSEEK_API_KEY is not set; export it or set it in .env "
            "(run `paper2kb` from writing-agent/ with the key available)"
        )
    return key


def base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL
