"""Tests for ai/provider.py — GroqProvider one-shot completion + error mapping."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import groq
import httpx
import pytest

from ai.provider import GroqProvider, LLMError, LLMResponse, LLMTransientError


def _make_response(content: dict, tokens_in: int = 100, tokens_out: int = 40) -> MagicMock:
    choice = MagicMock()
    choice.message.content = json.dumps(content)
    usage = MagicMock()
    usage.prompt_tokens = tokens_in
    usage.completion_tokens = tokens_out
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _provider(client: MagicMock) -> GroqProvider:
    prov = GroqProvider(client=client)
    prov._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]
    return prov


def test_complete_returns_llm_response() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _make_response(
        {"ok": True}, tokens_in=123, tokens_out=45
    )
    prov = _provider(client)

    out = prov.complete(
        model="qwen/qwen3-32b",
        system="sys",
        user="usr",
        max_tokens=512,
        temperature=0.1,
        timeout=30,
    )

    assert isinstance(out, LLMResponse)
    assert json.loads(out.content) == {"ok": True}
    assert out.model == "qwen/qwen3-32b"
    assert out.tokens_in == 123
    assert out.tokens_out == 45
    assert out.latency_ms >= 0
    # The model literal is passed straight through to the SDK.
    assert client.chat.completions.create.call_args.kwargs["model"] == "qwen/qwen3-32b"


def test_transient_error_is_translated() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = groq.APITimeoutError(
        request=httpx.Request("POST", "https://api.groq.com")
    )
    prov = _provider(client)

    with pytest.raises(LLMTransientError):
        prov.complete(
            model="llama-3.1-8b-instant",
            system="s",
            user="u",
            max_tokens=10,
            temperature=0.0,
            timeout=5,
        )


def test_non_transient_groq_error_is_translated() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = groq.GroqError("bad request")
    prov = _provider(client)

    with pytest.raises(LLMError) as exc:
        prov.complete(
            model="llama-3.1-8b-instant",
            system="s",
            user="u",
            max_tokens=10,
            temperature=0.0,
            timeout=5,
        )
    # Must NOT be the transient subclass.
    assert not isinstance(exc.value, LLMTransientError)


def test_rate_limiter_is_acquired() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _make_response({"ok": 1})
    prov = _provider(client)
    prov._rate_limiter.acquire = MagicMock()  # type: ignore[method-assign]

    prov.complete(
        model="llama-3.1-8b-instant",
        system="s",
        user="u",
        max_tokens=10,
        temperature=0.0,
        timeout=5,
    )
    prov._rate_limiter.acquire.assert_called_once()
