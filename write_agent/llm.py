"""DeepSeek API client with retry logic and structured output parsing.

Uses the OpenAI-compatible chat completions endpoint. Provides:
    - chat()          simple single-turn completion
    - chat_json()     forces structured JSON output (via prompt + parse)
    - retries with exponential backoff on transient errors
    - token accounting (input/output) for logging
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests


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
        # Claude Code capacity suffixes are not accepted by the Messages endpoint.
        self.model = re.sub(r"\[[^\]]+\]$", "", model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        last_exc: Exception | None = None
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
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
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
            raise LLMContentError(f"Unexpected API response shape: {e}. Raw: {str(data)[:300]}") from e

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
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
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
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


class ClaudeGatewayClient(DeepSeekClient):
    """Anthropic Messages-compatible client using Claude Code's gateway token."""

    def __init__(
        self,
        auth_token: str,
        base_url: str = "https://api.anthropic.com",
        model: str = "deepseek-v4-pro[1M]",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: int = 120,
        max_retries: int = 4,
    ):
        if not auth_token:
            raise LLMError("ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY is not set")
        self.api_key = auth_token
        self.base_url = base_url.rstrip("/")
        # Claude Code capacity suffixes are not accepted by the Messages endpoint.
        self.model = re.sub(r"\[[^\]]+\]$", "", model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "thinking": {"type": "disabled"},
        }
        if stop:
            payload["stop_sequences"] = stop
        data = self._post_anthropic(payload)
        try:
            content = "".join(
                str(block.get("text", ""))
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
            usage = data.get("usage", {})
            return LLMResult(
                text=content,
                usage_input=int(usage.get("input_tokens", 0)),
                usage_output=int(usage.get("output_tokens", 0)),
                model=data.get("model", self.model),
            )
        except (AttributeError, TypeError, ValueError) as e:
            raise LLMContentError(
                f"Unexpected gateway response shape: {e}. Raw: {str(data)[:300]}"
            ) from e

    def _post_anthropic(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/v1/messages"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    url, headers=self._headers(), json=payload, timeout=self.timeout
                )
                if resp.status_code in {402, 429}:
                    raise LLMRateLimitError(
                        f"gateway unavailable ({resp.status_code}): {resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    raise LLMError(f"gateway server error {resp.status_code}")
                if resp.status_code >= 400:
                    raise LLMError(f"gateway client error {resp.status_code}: {resp.text[:500]}")
                resp.raise_for_status()
                return resp.json()
            except (LLMRateLimitError, LLMError, requests.RequestException) as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 30))
        raise LLMError(f"LLM call failed after {self.max_retries} attempts: {last_exc}")


def gateway_auth_token() -> str:
    """Return a gateway credential from the process environment, never storage."""
    return (
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    )


def has_model_credential() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY") or gateway_auth_token())


def build_client(
    *,
    api_key: str = "",
    base_url: str = "https://api.deepseek.com/v1",
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    timeout: int = 120,
    max_retries: int = 4,
) -> DeepSeekClient:
    """Build the direct DeepSeek client or reuse Claude Code's working gateway."""
    direct_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if direct_key:
        return DeepSeekClient(
            api_key=direct_key,
            base_url=base_url,
            model=model or "deepseek-chat",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )
    token = gateway_auth_token()
    if token:
        return ClaudeGatewayClient(
            auth_token=token,
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
            model=(
                model
                if model and model != "deepseek-chat"
                else os.environ.get("ANTHROPIC_MODEL") or "deepseek-v4-pro[1M]"
            ),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )
    raise LLMError("No model credential is configured (DeepSeek direct or Claude gateway)")

# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _find_balanced_block(text: str, open_char: str, close_char: str, start: int = 0) -> int | None:
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


def extract_json(text: str) -> dict[str, Any] | None:
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


def extract_json_array(text: str) -> list[Any] | None:
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
