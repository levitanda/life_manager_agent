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


def _port_is_listening(port: int) -> bool:
    """Quick check whether anything is already bound on 127.0.0.1:port."""
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _allocate_port(used_ports: set[int]) -> int:
    start, end = _port_range_start(), _port_range_end()
    for p in range(start, end + 1):
        if p in used_ports:
            continue
        if _port_is_listening(p):
            # Some other process owns this port even though our DB doesn't
            # know about it — skip so we don't EADDRINUSE.
            continue
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


def start_bridge(user_id: int, pair_phone: Optional[str] = None) -> int:
    """Start (or recover) the bridge for a user. Returns the port number.

    `pair_phone` (digits-only) switches Baileys into pairing-code mode —
    the bridge must be cold-started (no live process) and the auth_session
    wiped so Baileys can request a code instead of attempting QR resume.
    """
    import db
    with db.session_scope() as s:
        row = _get_or_create_row(s, user_id)
        port = int(row.port)
        auth_dir = row.auth_dir

    # auth_dir in the DB may be relative (e.g. 'data/users/2/wa_auth').
    # The Python supervisor's CWD is the project root, but the bridge
    # subprocess runs with CWD=whatsapp_bridge/ — so the SAME relative
    # path resolves to two DIFFERENT folders. Without absolutising here,
    # the supervisor wipes A while Baileys loads stale creds from B and
    # then dies with 401 because WA already invalidated those credentials.
    auth_dir_abs = str(Path(auth_dir).resolve())

    # If pair mode is requested, force a clean slate: kill any live process
    # and wipe auth state so Baileys takes the "not registered" path.
    if pair_phone:
        existing = _processes.pop(user_id, None)
        if existing is not None and existing.poll() is None:
            try:
                os.killpg(os.getpgid(existing.pid), signal.SIGTERM)
                existing.wait(timeout=3)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(auth_dir_abs, ignore_errors=True)
            Path(auth_dir_abs).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("auth_dir wipe failed for user=%s: %s", user_id, e)
    else:
        # Re-use existing live process if we still have it
        p = _processes.get(user_id)
        if p is not None and p.poll() is None:
            logger.info("WA bridge already running for user=%s pid=%s", user_id, p.pid)
            return port

    log_path = _log_file(user_id)
    log_fp = open(log_path, "a", buffering=1, encoding="utf-8")
    log_fp.write(
        f"\n=== {datetime.datetime.utcnow().isoformat()} starting user={user_id} "
        f"port={port} pair={bool(pair_phone)} auth_dir={auth_dir_abs} ===\n"
    )
    env = os.environ.copy()
    env["BRIDGE_PORT"] = str(port)
    env["WA_AUTH_DIR"] = auth_dir_abs
    env["LOG_LEVEL"] = env.get("WA_LOG_LEVEL", "warn")
    if pair_phone:
        env["BRIDGE_PAIR_PHONE"] = "".join(c for c in pair_phone if c.isdigit())

    proc = subprocess.Popen(
        [_node_bin(), str(BRIDGE_SCRIPT)],
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        cwd=str(BRIDGE_SCRIPT.parent),
        start_new_session=True,
    )
    _processes[user_id] = proc
    logger.info(
        "Started WA bridge user=%s pid=%s port=%s pair=%s",
        user_id, proc.pid, port, bool(pair_phone),
    )

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


def request_pairing_code(user_id: int, phone: str, timeout_seconds: float = 45.0) -> dict:
    """Pair user `user_id` to WhatsApp number `phone` using the 8-char code flow.

    Cold-restarts the bridge with BRIDGE_PAIR_PHONE so Baileys requests the
    code before attempting QR; polls /pair until the code appears.

    Returns one of:
      {"ok": True, "code": "ABCD-1234"}
      {"ok": False, "error": "<reason>", "already_paired": True}
      {"ok": False, "error": "<reason>"}
    """
    import requests
    try:
        port = start_bridge(user_id, pair_phone=phone)
    except Exception as e:
        return {"ok": False, "error": f"bridge start failed: {e}"}

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_seconds
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            r = requests.post(f"{url}/pair", json={"phone": phone}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                return {"ok": True, "code": data.get("code") or data.get("raw")}
            if r.status_code == 409:
                return {"ok": False, "error": "already paired", "already_paired": True}
            if r.status_code == 503:
                last_err = "Baileys warming up"
                time.sleep(1)
                continue
            last_err = (
                r.json().get("error")
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            ) or f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
            time.sleep(1)
            continue
        time.sleep(1)
    return {"ok": False, "error": last_err or "timeout"}


def restore_running_bridges() -> int:
    """On bot startup: spawn a Node process for every user marked status='running' or 'qr_pending'.

    Rows with status='external' represent bridges that were started outside
    this supervisor (e.g. the long-running single-user bridge Daria stood
    up before the multi-tenant migration). We do NOT spawn anything for
    those — we just trust the existing process.
    """
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


def disable_for_user(user_id: int) -> bool:
    """Stop the user's bridge because they've lost access (sub cancelled, etc).

    Skips bridges marked status='external' — those are managed outside this
    supervisor (Daria's pre-migration single-user bridge). Returns True if a
    bridge was actually stopped, False if there was nothing to do or the row
    is external.
    """
    import db
    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        if row is None:
            return False
        if row.status == "external":
            logger.info("disable_for_user: skipping external bridge user=%s", user_id)
            return False
    try:
        stop_bridge(user_id)
        return True
    except Exception as e:
        logger.warning("disable_for_user: stop_bridge failed user=%s: %s", user_id, e)
        return False
