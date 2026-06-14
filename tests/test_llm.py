"""Tests for llm.py — Bedrock Converse wrapper."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_llm(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    import llm
    llm.reset_for_tests()
    yield
    llm.reset_for_tests()


# ─── Schema conversion ────────────────────────────────────────────────────────


def test_to_bedrock_tools_converts_anthropic_format():
    import llm
    anth = [{
        "name": "add_task",
        "description": "Add a task",
        "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
    }]
    out = llm._to_bedrock_tools(anth)
    assert out == [{
        "toolSpec": {
            "name": "add_task",
            "description": "Add a task",
            "inputSchema": {"json": {"type": "object", "properties": {"title": {"type": "string"}}}},
        }
    }]


def test_to_bedrock_tools_passthrough_when_already_bedrock():
    import llm
    bedrock = [{"toolSpec": {"name": "x", "description": "y", "inputSchema": {"json": {}}}}]
    assert llm._to_bedrock_tools(bedrock) == bedrock


def test_to_bedrock_tools_none():
    import llm
    assert llm._to_bedrock_tools(None) is None
    assert llm._to_bedrock_tools([]) is None


def test_to_bedrock_messages_plain_string():
    import llm
    msgs = [{"role": "user", "content": "привет"}]
    assert llm._to_bedrock_messages(msgs) == [
        {"role": "user", "content": [{"text": "привет"}]}
    ]


def test_to_bedrock_messages_with_tool_use_block():
    import llm
    msgs = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "calling tool"},
            {"type": "tool_use", "id": "t1", "name": "add_task", "input": {"title": "x"}},
        ],
    }]
    out = llm._to_bedrock_messages(msgs)
    assert out == [{
        "role": "assistant",
        "content": [
            {"text": "calling tool"},
            {"toolUse": {"toolUseId": "t1", "name": "add_task", "input": {"title": "x"}}},
        ],
    }]


def test_to_bedrock_messages_with_tool_result_block():
    import llm
    msgs = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ],
    }]
    out = llm._to_bedrock_messages(msgs)
    assert out[0]["content"][0]["toolResult"]["toolUseId"] == "t1"
    assert out[0]["content"][0]["toolResult"]["content"] == [{"text": "ok"}]
    assert out[0]["content"][0]["toolResult"]["status"] == "success"


def test_to_bedrock_messages_tool_result_error_flag():
    import llm
    msgs = [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "bad", "is_error": True}],
    }]
    out = llm._to_bedrock_messages(msgs)
    assert out[0]["content"][0]["toolResult"]["status"] == "error"


def test_parse_bedrock_response_text_only():
    import llm
    raw = {
        "output": {"message": {"content": [{"text": "hello world"}]}},
        "stopReason": "end_turn",
    }
    parsed = llm._parse_bedrock_response(raw)
    assert parsed["text"] == "hello world"
    assert parsed["tool_uses"] == []
    assert parsed["stop_reason"] == "end_turn"


def test_parse_bedrock_response_with_tool_use():
    import llm
    raw = {
        "output": {"message": {"content": [
            {"text": "let me check"},
            {"toolUse": {"toolUseId": "abc", "name": "get_weather", "input": {"city": "Haifa"}}},
        ]}},
        "stopReason": "tool_use",
    }
    parsed = llm._parse_bedrock_response(raw)
    assert parsed["text"] == "let me check"
    assert parsed["tool_uses"] == [{"id": "abc", "name": "get_weather", "input": {"city": "Haifa"}}]
    assert parsed["stop_reason"] == "tool_use"


# ─── chat() with mocked boto3 ─────────────────────────────────────────────────


def test_chat_calls_bedrock_with_correct_shape():
    import llm
    fake = MagicMock()
    fake.converse.return_value = {
        "output": {"message": {"content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
    }
    with patch.object(llm, "_client", return_value=fake):
        out = llm.chat(
            "meta.llama3-3-70b-instruct-v1:0",
            "you are helpful",
            [{"role": "user", "content": "hi"}],
            max_tokens=512,
        )
    fake.converse.assert_called_once()
    kwargs = fake.converse.call_args.kwargs
    assert kwargs["modelId"] == "meta.llama3-3-70b-instruct-v1:0"
    assert kwargs["system"] == [{"text": "you are helpful"}]
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 512}
    assert "toolConfig" not in kwargs
    assert out["text"] == "ok"


def test_chat_with_tools_attaches_toolconfig():
    import llm
    fake = MagicMock()
    fake.converse.return_value = {
        "output": {"message": {"content": [{"text": ""}]}},
        "stopReason": "end_turn",
    }
    tools = [{"name": "x", "description": "y", "input_schema": {"type": "object"}}]
    with patch.object(llm, "_client", return_value=fake):
        llm.chat("amazon.nova-lite-v1:0", "sys", [{"role": "user", "content": "hi"}], tools=tools)
    kwargs = fake.converse.call_args.kwargs
    assert "toolConfig" in kwargs
    assert kwargs["toolConfig"]["tools"][0]["toolSpec"]["name"] == "x"


def test_chat_passes_temperature():
    import llm
    fake = MagicMock()
    fake.converse.return_value = {
        "output": {"message": {"content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
    }
    with patch.object(llm, "_client", return_value=fake):
        llm.chat("amazon.nova-lite-v1:0", "sys", [{"role": "user", "content": "hi"}], temperature=0.3)
    kwargs = fake.converse.call_args.kwargs
    assert kwargs["inferenceConfig"]["temperature"] == 0.3


# ─── Fallback ─────────────────────────────────────────────────────────────────


def test_anthropic_fallback_routes_through_sdk(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    import llm

    fake_resp = MagicMock(stop_reason="end_turn")
    fake_resp.content = [MagicMock(type="text", text="привет")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    with patch("anthropic.Anthropic", return_value=fake_client):
        out = llm.chat(
            "anthropic.claude-sonnet-4-6-v1:0",
            "sys",
            [{"role": "user", "content": "hi"}],
        )

    # Routed to Anthropic SDK, not Bedrock
    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert out["text"] == "привет"


def test_anthropic_fallback_with_tool_use():
    os.environ["LLM_FALLBACK_PROVIDER"] = "anthropic"
    os.environ["ANTHROPIC_API_KEY"] = "test"
    try:
        import llm
        fake_resp = MagicMock(stop_reason="tool_use")
        tu_block = MagicMock(type="tool_use", id="abc", input={"title": "x"})
        tu_block.name = "add_task"  # MagicMock(name=...) clashes; set explicitly
        fake_resp.content = [tu_block]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp
        with patch("anthropic.Anthropic", return_value=fake_client):
            out = llm.chat(
                "meta.llama3-3-70b-instruct-v1:0",  # non-Anthropic id in fallback → defaults to Sonnet
                "sys",
                [{"role": "user", "content": "hi"}],
                tools=[{"name": "add_task", "description": "x", "input_schema": {"type": "object"}}],
            )
        assert out["tool_uses"] == [{"id": "abc", "name": "add_task", "input": {"title": "x"}}]
    finally:
        os.environ.pop("LLM_FALLBACK_PROVIDER", None)


# ─── Bedrock error path ───────────────────────────────────────────────────────


def test_chat_propagates_bedrock_error():
    import llm
    fake = MagicMock()
    fake.converse.side_effect = RuntimeError("ResourceNotFound: model not enabled")
    with patch.object(llm, "_client", return_value=fake):
        with pytest.raises(RuntimeError, match="ResourceNotFound"):
            llm.chat("bad-model", "sys", [{"role": "user", "content": "hi"}])
