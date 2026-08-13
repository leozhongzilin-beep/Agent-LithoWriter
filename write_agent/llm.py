"""DeepSeek API client with retry logic and structured output parsing.

Uses the OpenAI-compatible chat completions endpoint. Provides:
    - chat()          simple single-turn completion
    - chat_json()     forces structured JSON output (via prompt + parse)
    - retries with exponential backoff on transient errors
    - token accounting (input/output) for logging
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from .config import Config


class LLMError(Exception):
    """Base error for LLM calls."""


class LLMRateLimitError(LLMError):
    pass


class LLMContentError(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    usage_input: int = 0
    usage_output: int = 0
    model: str = ""


class DeepSeekClient:
    """Thin OpenAI-compatible chat client for DeepSeek."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: int = 120,
        max_retries: int = 4,
    ):
        if not api_key:
            raise LLMError(
                "DEEPSEEK_API_KEY not set. "
                "Copy .env.example to .env and fill in your key, "
                "or export DEEPSEEK_API_KEY."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    url, headers=self._headers(), json=payload, timeout=self.timeout
                )
                if resp.status_code == 429:
                    wait = min(2 ** attempt, 30)
                    raise LLMRateLimitError(
                        f"rate limited (429), retry in {wait}s: {resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    wait = min(2 ** attempt, 30)
                    raise LLMError(f"server error {resp.status_code}, retry in {wait}s")
                resp.raise_for_status()
                return resp.json()
            except (LLMRateLimitError, LLMError) as e:
                last_exc = e
                wait = min(2 ** attempt, 30)
                time.sleep(wait)
            except requests.RequestException as e:
                last_exc = e
                wait = min(2 ** attempt, 30)
                time.sleep(wait)
        raise LLMError(f"LLM call failed after {self.max_retries} attempts: {last_exc}")

    def chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[List[str]] = None,
    ) -> LLMResult:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if stop:
            payload["stop"] = stop
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return LLMResult(
                text=content or "",
                usage_input=int(usage.get("prompt_tokens", 0)),
                usage_output=int(usage.get("completion_tokens", 0)),
                model=data.get("model", self.model),
            )
        except (KeyError, IndexError, TypeError) as e:
            raise LLMContentError(f"Unexpected API response shape: {e}. Raw: {str(data)[:300]}")

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Request a JSON object from the model. Tries several parse strategies.

        The model is asked to wrap JSON in ```json ... ``` fences; we also
        fall back to finding the first balanced {...} block in the response.
        """
        fenced_user = (
            user
            + "\n\nIMPORTANT: Respond with ONLY a single valid JSON object. "
            "Do NOT include any other text, markdown, or explanation. "
            "Wrap the JSON in ```json ... ``` fences."
        )
        result = self.chat(
            system=system,
            user=fenced_user,
            temperature=0.2 if temperature is None else temperature,
            max_tokens=max_tokens,
        )
        obj = extract_json(result.text)
        if obj is None:
            raise LLMContentError(
                f"Could not parse JSON from model response. Response head:\n{result.text[:400]}"
            )
        return obj

    def chat_json_list(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        fenced_user = (
            user
            + "\n\nIMPORTANT: Respond with ONLY a valid JSON array of objects. "
            "Do NOT include any other text. Wrap in ```json ... ``` fences."
        )
        result = self.chat(
            system=system,
            user=fenced_user,
            temperature=0.2 if temperature is None else temperature,
            max_tokens=max_tokens,
        )
        arr = extract_json_array(result.text)
        if arr is None:
            raise LLMContentError(
                f"Could not parse JSON array from model response. Response head:\n{result.text[:400]}"
            )
        return arr


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _find_balanced_block(text: str, open_char: str, close_char: str, start: int = 0) -> Optional[int]:
    """Return index just after the matching close_char, or None."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == open_char:
            depth += 1
        elif c == close_char:
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from model output."""
    text = text.strip()
    if not text:
        return None

    # Try direct parse first
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fence_re = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_re:
        try:
            val = json.loads(fence_re.group(1))
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass

    # Find first balanced { ... } block
    start = text.find("{")
    while start != -1:
        end = _find_balanced_block(text, "{", "}", start)
        if end is not None:
            try:
                val = json.loads(text[start:end])
                if isinstance(val, dict):
                    return val
            except json.JSONDecodeError:
                pass
        start = text.find("{", start + 1)

    return None


def extract_json_array(text: str) -> Optional[List[Any]]:
    """Extract the first JSON array from model output."""
    text = text.strip()
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return val
    except json.JSONDecodeError:
        pass

    fence_re = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_re:
        try:
            val = json.loads(fence_re.group(1))
            if isinstance(val, list):
                return val
        except json.JSONDecodeError:
            pass

    start = text.find("[")
    while start != -1:
        end = _find_balanced_block(text, "[", "]", start)
        if end is not None:
            try:
                val = json.loads(text[start:end])
                if isinstance(val, list):
                    return val
            except json.JSONDecodeError:
                pass
        start = text.find("[", start + 1)

    return None
