"""Unified LLM client for the life-agent.

Wraps AWS Bedrock Converse API so every model (Llama 3.3 70B, Amazon Nova
Lite, Nova Pro, Claude on Bedrock, …) is reached through the same call:

    llm.chat(model, system, messages, tools=None, max_tokens=2048) -> dict

Returns a normalized response:

    {
        "text": str,             # plain-text portion of the reply
        "tool_uses": [           # tool-use blocks from the model
            {"id": str, "name": str, "input": dict},
            ...
        ],
        "stop_reason": str,      # 'end_turn' | 'tool_use' | 'max_tokens' | ...
        "raw": dict,             # original Bedrock response, for debugging
    }

Model IDs we use (must be enabled in the AWS account, region eu-central-1):
- Llama 3.3 70B Instruct: 'meta.llama3-3-70b-instruct-v1:0'
- Amazon Nova Lite:       'amazon.nova-lite-v1:0'
- Amazon Nova Pro:        'amazon.nova-pro-v1:0'
- Claude Haiku 4.5:       'anthropic.claude-haiku-4-5-20251001-v1:0'
- Claude Sonnet 4.6:      'anthropic.claude-sonnet-4-6-v1:0'

Fallback to direct Anthropic API: set `LLM_FALLBACK_PROVIDER=anthropic`
(default off). Useful for local dev without AWS credentials.

Tool-use format follows the Bedrock Converse spec:
    tools = [{
        "toolSpec": {
            "name": "add_task",
            "description": "...",
            "inputSchema": {"json": {"type": "object", ...}},
        }
    }, ...]

The agent code passes Anthropic-style tool schemas; `_to_bedrock_tools`
converts them transparently.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Lazy boto3 client ────────────────────────────────────────────────────────

_bedrock = None


def _client():
    global _bedrock
    if _bedrock is None:
        import boto3
        region = os.environ.get("AWS_REGION", "eu-central-1")
        _bedrock = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock


def reset_for_tests() -> None:
    global _bedrock
    _bedrock = None


# ─── Schema conversion ───────────────────────────────────────────────────────


def _to_bedrock_tools(anthropic_tools: Optional[list[dict]]) -> Optional[list[dict]]:
    """Convert Anthropic-style tool schemas to Bedrock Converse format.

    Anthropic shape:
        {"name": "add_task", "description": "...", "input_schema": {...}}

    Bedrock shape:
        {"toolSpec": {"name": "add_task", "description": "...",
                      "inputSchema": {"json": {...}}}}
    """
    if not anthropic_tools:
        return None
    out = []
    for t in anthropic_tools:
        if "toolSpec" in t:  # already Bedrock-shaped
            out.append(t)
            continue
        out.append({
            "toolSpec": {
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": {"json": t["input_schema"]},
            }
        })
    return out


def _to_bedrock_messages(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-style messages list to Bedrock Converse format.

    Anthropic content can be a plain string or a list of blocks (text /
    tool_use / tool_result). Bedrock uses {"content": [{"text": "..."}]} or
    {"content": [{"toolUse": {...}}]} etc.
    """
    out = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            blocks = [{"text": content}]
        else:
            blocks = []
            for b in content:
                # Anthropic block dicts
                if isinstance(b, dict):
                    btype = b.get("type")
                    if btype == "text":
                        blocks.append({"text": b["text"]})
                    elif btype == "tool_use":
                        blocks.append({
                            "toolUse": {
                                "toolUseId": b["id"],
                                "name": b["name"],
                                "input": b.get("input", {}),
                            }
                        })
                    elif btype == "tool_result":
                        result_content = b.get("content", "")
                        if isinstance(result_content, str):
                            inner = [{"text": result_content}]
                        else:
                            inner = result_content
                        blocks.append({
                            "toolResult": {
                                "toolUseId": b["tool_use_id"],
                                "content": inner,
                                "status": "error" if b.get("is_error") else "success",
                            }
                        })
                    else:
                        blocks.append({"text": str(b)})
                else:
                    # SDK objects: convert via duck-typing
                    btype = getattr(b, "type", None)
                    if btype == "text":
                        blocks.append({"text": b.text})
                    elif btype == "tool_use":
                        blocks.append({
                            "toolUse": {
                                "toolUseId": b.id,
                                "name": b.name,
                                "input": b.input or {},
                            }
                        })
                    else:
                        blocks.append({"text": str(b)})
        out.append({"role": role, "content": blocks})
    return out


def _parse_bedrock_response(resp: dict) -> dict:
    """Normalize a Bedrock Converse response into our unified shape."""
    out_msg = resp.get("output", {}).get("message", {})
    blocks = out_msg.get("content", [])
    text_parts = []
    tool_uses = []
    for b in blocks:
        if "text" in b:
            text_parts.append(b["text"])
        elif "toolUse" in b:
            tu = b["toolUse"]
            tool_uses.append({
                "id": tu["toolUseId"],
                "name": tu["name"],
                "input": tu.get("input", {}),
            })
    return {
        "text": "".join(text_parts).strip(),
        "tool_uses": tool_uses,
        "stop_reason": resp.get("stopReason", ""),
        "raw": resp,
    }


# ─── Public API ───────────────────────────────────────────────────────────────


def chat(
    model: str,
    system: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 2048,
    temperature: Optional[float] = None,
) -> dict:
    """Send a Converse request and return a normalized response.

    `model` must be a Bedrock model id (e.g. 'meta.llama3-3-70b-instruct-v1:0').
    `messages` follows the Anthropic shape (role + content); conversion is
    automatic. `tools` may be passed in Anthropic shape too.
    """
    if os.environ.get("LLM_FALLBACK_PROVIDER") == "anthropic":
        return _anthropic_fallback(model, system, messages, tools, max_tokens, temperature)

    kwargs: dict[str, Any] = {
        "modelId": model,
        "system": [{"text": system}] if system else [],
        "messages": _to_bedrock_messages(messages),
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if temperature is not None:
        kwargs["inferenceConfig"]["temperature"] = temperature
    bedrock_tools = _to_bedrock_tools(tools)
    if bedrock_tools:
        kwargs["toolConfig"] = {"tools": bedrock_tools}

    resp = _client().converse(**kwargs)
    return _parse_bedrock_response(resp)


# ─── Fallback for dev / migration safety ─────────────────────────────────────


def _anthropic_fallback(
    model: str,
    system: str,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
    temperature: Optional[float],
) -> dict:
    """Route the call to the Anthropic API directly. Translates the Bedrock
    model id back to an Anthropic model id when applicable.
    """
    import anthropic
    import config

    # Map Bedrock id → Anthropic id (best-effort)
    mapping = {
        "anthropic.claude-haiku-4-5-20251001-v1:0": "claude-haiku-4-5-20251001",
        "anthropic.claude-sonnet-4-6-v1:0": "claude-sonnet-4-6",
        # For non-Anthropic models in fallback mode, default to Sonnet
    }
    aid = mapping.get(model, "claude-sonnet-4-6")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs: dict[str, Any] = {
        "model": aid,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        # Pass Anthropic-shaped tools through
        kwargs["tools"] = [
            t["toolSpec"] if "toolSpec" in t else t for t in tools
        ]

    resp = client.messages.create(**kwargs)

    text_parts, tool_uses = [], []
    for b in resp.content:
        if b.type == "text":
            text_parts.append(b.text)
        elif b.type == "tool_use":
            tool_uses.append({"id": b.id, "name": b.name, "input": b.input or {}})
    return {
        "text": "".join(text_parts).strip(),
        "tool_uses": tool_uses,
        "stop_reason": resp.stop_reason or "",
        "raw": resp.model_dump() if hasattr(resp, "model_dump") else None,
    }


# ─── Model registry ───────────────────────────────────────────────────────────

MODEL_LLAMA_70B = "meta.llama3-3-70b-instruct-v1:0"
MODEL_NOVA_LITE = "amazon.nova-lite-v1:0"
MODEL_NOVA_PRO = "amazon.nova-pro-v1:0"
MODEL_HAIKU_BEDROCK = "anthropic.claude-haiku-4-5-20251001-v1:0"
MODEL_SONNET_BEDROCK = "anthropic.claude-sonnet-4-6-v1:0"
