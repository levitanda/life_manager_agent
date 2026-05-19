"""A2A (Agent2Agent) server for life-agent.

Exposes a small read-only subset of tools to other agents (whitelisted by
API key). Implements the A2A protocol's minimal surface:
  GET  /.well-known/agent.json   — Agent Card (public, no secrets)
  POST /a2a/tasks/send           — JSON-RPC-style task dispatch (auth)
  GET  /a2a/health               — liveness probe (public)

Auth: Bearer token. Keys are stored as sha256 hashes in a2a_clients.json;
raw tokens never persisted. Add/remove clients via the CLI at the bottom
of this file.

The set of exposed tools is intentionally hard-coded read-only — even if
a client's allow-list says otherwise, A2A_EXPOSED_TOOLS gates every call.
"""

import argparse
import hashlib
import json
import logging
import os
import secrets
import sys
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, request
from jsonschema import validate, ValidationError

import config
import tools

logger = logging.getLogger(__name__)

CLIENTS_FILE = Path(__file__).parent / "a2a_clients.json"

# Hard cap: even if a client's allow-list contains a write-tool, the server
# refuses to dispatch anything outside this set.
A2A_EXPOSED_TOOLS = {
    "show_tasks",
    "get_weather",
    "find_free_time",
    "list_scheduled_actions",
}


# ─── client registry ─────────────────────────────────────────────────────────

def _load_clients() -> dict:
    if not CLIENTS_FILE.exists():
        return {}
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("a2a_clients.json load failed: %s", e)
        return {}


def _save_clients(data: dict) -> None:
    CLIENTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Tight perms — keys are sensitive even as hashes
    try:
        os.chmod(CLIENTS_FILE, 0o600)
    except Exception:
        pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _find_client(token: str) -> dict | None:
    if not token:
        return None
    digest = _hash_token(token)
    for client_id, data in _load_clients().items():
        if data.get("api_key_hash") == digest:
            return {"id": client_id, **data}
    return None


# ─── Flask app ───────────────────────────────────────────────────────────────

app = Flask(__name__)
PUBLIC_PATHS = {"/.well-known/agent.json", "/a2a/health"}


@app.before_request
def _authenticate():
    if request.path in PUBLIC_PATHS:
        return None
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not token:
        return jsonify({"error": "missing_token"}), 401
    client = _find_client(token)
    if not client:
        return jsonify({"error": "invalid_token"}), 403
    g.client = client
    return None


@app.get("/a2a/health")
def health():
    return jsonify({"status": "ok", "exposed_tools": sorted(A2A_EXPOSED_TOOLS)})


@app.get("/.well-known/agent.json")
def agent_card():
    base_url = os.environ.get("A2A_AGENT_URL", "").rstrip("/")
    skills = []
    for schema in tools.TOOL_SCHEMAS:
        if schema["name"] in A2A_EXPOSED_TOOLS:
            skills.append({
                "id": schema["name"],
                "name": schema["name"],
                "description": schema.get("description", ""),
                "inputSchema": schema.get("input_schema", {}),
            })
    return jsonify({
        "name": os.environ.get("A2A_AGENT_NAME", "Daria's Life Agent"),
        "description": "Personal assistant exposing read-only scheduling, "
                       "weather, and availability queries to whitelisted agents.",
        "url": f"{base_url}/a2a" if base_url else "/a2a",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "authentication": {
            "schemes": ["bearer"],
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
    })


@app.post("/a2a/tasks/send")
def tasks_send():
    body = request.get_json(silent=True) or {}
    meta = body.get("metadata") or {}
    tool_name = meta.get("tool")
    params = meta.get("params") or {}

    if not tool_name or not isinstance(tool_name, str):
        return jsonify({"error": "metadata.tool required (string)"}), 400

    # Server-side hard cap
    if tool_name not in A2A_EXPOSED_TOOLS:
        return jsonify({"error": "tool_not_exposed", "tool": tool_name}), 403

    # Client-side allow-list
    client_allowed = set(g.client.get("allowed_tools") or [])
    if tool_name not in client_allowed:
        return jsonify({"error": "tool_not_allowed_for_client", "tool": tool_name}), 403

    schema = next((s for s in tools.TOOL_SCHEMAS if s["name"] == tool_name), None)
    if not schema:
        return jsonify({"error": "tool_unknown"}), 404

    # Validate params against the tool's input schema
    try:
        validate(instance=params, schema=schema["input_schema"])
    except ValidationError as ve:
        return jsonify({"error": "invalid_params", "detail": ve.message}), 400

    fn = tools.TOOL_FUNCS.get(tool_name)
    if not fn:
        return jsonify({"error": "tool_not_implemented"}), 500

    try:
        result = fn(**params, _context=None, _active_tasks=None)
    except Exception as e:
        logger.exception("A2A tool %s failed: %s", tool_name, e)
        return jsonify({"error": "tool_error", "detail": str(e)}), 500

    task_id = body.get("id") or str(uuid.uuid4())
    return jsonify({
        "id": task_id,
        "status": "completed",
        "result": result,
        "tool": tool_name,
        "client": g.client.get("name"),
    })


@app.post("/a2a/tasks/get/<task_id>")
def tasks_get(task_id: str):
    # MVP: synchronous only. Persist task results in future; for now reply 404.
    return jsonify({"error": "tasks_get_not_implemented", "id": task_id}), 404


def run(host: str = "0.0.0.0", port: int | None = None) -> None:
    """Start the server (used by systemd unit)."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] a2a_server: %(message)s",
        level=logging.INFO,
    )
    p = port or int(os.environ.get("A2A_PORT", "5001"))
    logger.info("A2A server listening on %s:%s", host, p)
    app.run(host=host, port=p, use_reloader=False)


# ─── CLI for client management ───────────────────────────────────────────────

def _cmd_add_client(args):
    clients = _load_clients()
    requested = [t.strip() for t in (args.tools or "").split(",") if t.strip()]
    invalid = [t for t in requested if t not in A2A_EXPOSED_TOOLS]
    if invalid:
        print(f"ERROR: these tools are not exposed by the server: {invalid}", file=sys.stderr)
        print(f"Allowed tools: {sorted(A2A_EXPOSED_TOOLS)}", file=sys.stderr)
        sys.exit(2)
    if not requested:
        requested = sorted(A2A_EXPOSED_TOOLS)

    raw_key = "A2A_" + secrets.token_urlsafe(32)
    client_id = uuid.uuid4().hex[:12]
    clients[client_id] = {
        "name": args.name,
        "api_key_hash": _hash_token(raw_key),
        "allowed_tools": requested,
    }
    _save_clients(clients)
    print(f"Created client {client_id} ({args.name}).")
    print(f"Tools: {requested}")
    print(f"API key (give this to the other agent — NOT stored anywhere else): {raw_key}")


def _cmd_list_clients(_args):
    clients = _load_clients()
    if not clients:
        print("No clients configured.")
        return
    for cid, data in clients.items():
        print(f"  [{cid}] {data.get('name')} — tools: {data.get('allowed_tools')}")


def _cmd_remove_client(args):
    clients = _load_clients()
    target = None
    for cid, data in clients.items():
        if cid == args.id or data.get("name") == args.id:
            target = cid
            break
    if not target:
        print(f"No client with id or name '{args.id}'", file=sys.stderr)
        sys.exit(1)
    name = clients[target].get("name")
    del clients[target]
    _save_clients(clients)
    print(f"Removed client {target} ({name}).")


def _main():
    parser = argparse.ArgumentParser(description="A2A server + client management")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="Run the A2A server")

    p_add = sub.add_parser("add-client", help="Add a new whitelisted client")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--tools", help="Comma-separated allow-list. Default: all exposed.")
    p_add.set_defaults(func=_cmd_add_client)

    p_list = sub.add_parser("list-clients")
    p_list.set_defaults(func=_cmd_list_clients)

    p_rm = sub.add_parser("remove-client")
    p_rm.add_argument("id", help="Client id or name")
    p_rm.set_defaults(func=_cmd_remove_client)

    args = parser.parse_args()
    if args.cmd == "serve" or args.cmd is None:
        run()
    else:
        args.func(args)


if __name__ == "__main__":
    _main()
