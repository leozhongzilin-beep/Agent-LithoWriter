"""Environment plumbing for the LLM client (same convention as write_agent)."""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def api_key() -> str:
    """Direct key when present; gateway credentials are resolved by write_agent."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    gateway = (
        os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    if not key and not gateway:
        raise OSError(
            "No model credential is configured; set DEEPSEEK_API_KEY or run "
            "inside the credentialed Claude Code gateway"
        )
    return key


def base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL
