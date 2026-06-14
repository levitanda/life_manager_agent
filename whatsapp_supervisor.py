"""Per-user WhatsApp bridge supervisor.

Each user who has WhatsApp enabled gets their own Node.js Baileys process
listening on a private 127.0.0.1 port. The supervisor:

  - Allocates a free port from PORT_RANGE per user.
  - Creates an isolated auth_session directory under data/users/{id}/wa_auth/.
  - Spawns the bridge with env vars BRIDGE_PORT and WA_AUTH_DIR.
  - Tracks the process in memory + persists the port/status in the
    whatsapp_bridges table.
  - On bot startup, restores all bridges marked status='running'.

Public API:

    start_bridge(user_id) -> int           # returns the allocated port
    stop_bridge(user_id)
    restart_bridge(user_id)
    is_running(user_id) -> bool
    get_qr(user_id) -> str | None
    restore_running_bridges() -> int
    shutdown_all()

QR delivery to Telegram is the caller's responsibility — typically the
settings menu polls get_qr after start_bridge until it returns None
(meaning the device has paired) or a timeout fires.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


BRIDGE_SCRIPT = Path(__file__).resolve().parent / "whatsapp_bridge" / "server.js"


def _port_range_start() -> int:
    return int(os.environ.get("WA_PORT_RANGE_START", "3030"))


def _port_range_end() -> int:
    return int(os.environ.get("WA_PORT_RANGE_END", "3099"))


def _node_bin() -> str:
    return os.environ.get("NODE_BIN", "node")

# In-memory registry: user_id → subprocess.Popen
_processes: dict[int, subprocess.Popen] = {}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _auth_dir(user_id: int) -> Path:
    import db
    p = Path(db.data_dir(), "users", str(user_id), "wa_auth")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_file(user_id: int) -> Path:
    import db
    return Path(db.data_dir(), "users", str(user_id), "wa_bridge.log")


def _allocate_port(used_ports: set[int]) -> int:
    start, end = _port_range_start(), _port_range_end()
    for p in range(start, end + 1):
        if p not in used_ports:
            return p
    raise RuntimeError(f"No free WhatsApp bridge port in {start}-{end}")


# ─── DB row helpers ──────────────────────────────────────────────────────────


def _get_or_create_row(session, user_id: int):
    import db
    row = session.get(db.WhatsAppBridge, user_id)
    if row is not None:
        return row
    used = {r.port for r in session.query(db.WhatsAppBridge).all()}
    port = _allocate_port(used)
    auth = str(_auth_dir(user_id))
    row = db.WhatsAppBridge(user_id=user_id, port=port, auth_dir=auth, status="stopped")
    session.add(row)
    session.flush()
    return row


# ─── Public API ──────────────────────────────────────────────────────────────


def start_bridge(user_id: int) -> int:
    """Start (or recover) the bridge for a user. Returns the port number."""
    import db
    with db.session_scope() as s:
        row = _get_or_create_row(s, user_id)
        port = int(row.port)
        auth_dir = row.auth_dir

    # Re-use existing live process if we still have it
    p = _processes.get(user_id)
    if p is not None and p.poll() is None:
        logger.info("WA bridge already running for user=%s pid=%s", user_id, p.pid)
        return port

    log_path = _log_file(user_id)
    log_fp = open(log_path, "a", buffering=1, encoding="utf-8")
    log_fp.write(f"\n=== {datetime.datetime.utcnow().isoformat()} starting user={user_id} port={port} ===\n")
    env = os.environ.copy()
    env["BRIDGE_PORT"] = str(port)
    env["WA_AUTH_DIR"] = auth_dir
    env["LOG_LEVEL"] = env.get("WA_LOG_LEVEL", "warn")

    proc = subprocess.Popen(
        [_node_bin(), str(BRIDGE_SCRIPT)],
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        cwd=str(BRIDGE_SCRIPT.parent),
        start_new_session=True,  # so we can signal the whole process group
    )
    _processes[user_id] = proc
    logger.info("Started WA bridge user=%s pid=%s port=%s", user_id, proc.pid, port)

    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        row.status = "qr_pending"
        row.last_started_at = datetime.datetime.utcnow()
    return port


def stop_bridge(user_id: int, grace_seconds: float = 5.0) -> bool:
    """Send SIGTERM and wait briefly. Returns True if the process is now down."""
    import db
    p = _processes.pop(user_id, None)
    if p is None or p.poll() is not None:
        with db.session_scope() as s:
            row = s.get(db.WhatsAppBridge, user_id)
            if row:
                row.status = "stopped"
        return True
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=2)
    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        if row:
            row.status = "stopped"
    logger.info("Stopped WA bridge user=%s", user_id)
    return True


def restart_bridge(user_id: int) -> int:
    stop_bridge(user_id)
    return start_bridge(user_id)


def is_running(user_id: int) -> bool:
    p = _processes.get(user_id)
    return p is not None and p.poll() is None


def get_qr(user_id: int, timeout_seconds: float = 30.0) -> Optional[str]:
    """Poll the bridge until it returns a QR string. None if the device is already paired
    (so no QR is needed) or the bridge isn't running.
    """
    import requests
    import db
    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        if row is None:
            return None
        port = int(row.port)

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            s = requests.get(f"{url}/status", timeout=3).json()
            if s.get("ready"):
                return None  # paired, no QR needed
            if s.get("has_qr"):
                qr = requests.get(f"{url}/qr", timeout=3).json().get("qr")
                if qr:
                    return qr
        except Exception:
            pass
        time.sleep(1)
    return None


def restore_running_bridges() -> int:
    """On bot startup: spawn a Node process for every user marked status='running' or 'qr_pending'."""
    import db
    started = 0
    with db.session_scope() as s:
        rows = (
            s.query(db.WhatsAppBridge)
            .filter(db.WhatsAppBridge.status.in_(["running", "qr_pending"]))
            .all()
        )
        user_ids = [r.user_id for r in rows]
    for uid in user_ids:
        try:
            start_bridge(uid)
            started += 1
        except Exception as e:
            logger.warning("Failed to restore WA bridge for user=%s: %s", uid, e)
    return started


def mark_running(user_id: int) -> None:
    """Caller flips status='running' once the user has confirmed pairing."""
    import db
    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        if row:
            row.status = "running"


def shutdown_all() -> None:
    """Terminate every supervised process cleanly. Call on bot shutdown."""
    for uid in list(_processes.keys()):
        try:
            stop_bridge(uid, grace_seconds=2.0)
        except Exception as e:
            logger.warning("shutdown_all: failed to stop user=%s: %s", uid, e)
