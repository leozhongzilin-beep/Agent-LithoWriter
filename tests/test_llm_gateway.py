"""Credential-safe tests for the Claude Code gateway transport."""

from __future__ import annotations

import pytest
from write_agent.llm import (
    ClaudeGatewayClient,
    DeepSeekClient,
    LLMError,
    build_client,
    has_model_credential,
)


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "model": "deepseek-v4-flash",
            "content": [{"type": "text", "text": "gateway response"}],
            "usage": {"input_tokens": 17, "output_tokens": 5},
        }


def test_gateway_messages_protocol_and_usage(monkeypatch):
    seen = {}

    def post(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return _Response()

    client = ClaudeGatewayClient(
        auth_token="test-token",
        base_url="https://gateway.invalid",
        model="deepseek/deepseek-v4-flash[1m]",
        max_retries=1,
    )
    monkeypatch.setattr(client.session, "post", post)

    result = client.chat("system", "user", stop=["END"])

    assert seen["url"] == "https://gateway.invalid/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-token"
    assert seen["headers"]["Authorization"] == "Bearer test-token"
    assert seen["payload"]["model"] == "deepseek/deepseek-v4-flash"
    assert seen["payload"]["system"] == "system"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "user"}]
    assert seen["payload"]["thinking"] == {"type": "disabled"}
    assert seen["payload"]["stop_sequences"] == ["END"]
    assert result.text == "gateway response"
    assert (result.usage_input, result.usage_output) == (17, 5)


def test_factory_reuses_gateway_and_active_model(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.invalid")
    monkeypatch.setenv("ANTHROPIC_MODEL", "deepseek-v4-flash")

    client = build_client(model="deepseek-chat")

    assert isinstance(client, ClaudeGatewayClient)
    assert client.base_url == "https://gateway.invalid"
    assert client.model == "deepseek-v4-flash"
    assert has_model_credential() is True


def test_factory_prefers_direct_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "direct-test-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gateway-test-token")

    client = build_client(model="deepseek-chat")

    assert type(client) is DeepSeekClient


def test_factory_fails_closed_without_credentials(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LLMError, match="No model credential"):
        build_client()
