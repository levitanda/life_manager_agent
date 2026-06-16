"""Tests for whatsapp_supervisor.py with subprocess mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WA_PORT_RANGE_START", "3030")
    monkeypatch.setenv("WA_PORT_RANGE_END", "3033")  # small range for collision tests
    import db, crypto, whatsapp_supervisor
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    whatsapp_supervisor._processes.clear()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()
    whatsapp_supervisor._processes.clear()


def _make_user(uid: int = 1):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=uid * 10, telegram_chat_id=uid * 10)
        return u.id


# ─── Port allocation ──────────────────────────────────────────────────────────


def test_allocate_port_picks_first_free():
    import whatsapp_supervisor
    assert whatsapp_supervisor._allocate_port(set()) == 3030
    assert whatsapp_supervisor._allocate_port({3030}) == 3031


def test_allocate_port_raises_when_exhausted():
    import whatsapp_supervisor
    used = set(range(3030, 3034))
    with pytest.raises(RuntimeError, match="No free"):
        whatsapp_supervisor._allocate_port(used)


# ─── start_bridge ─────────────────────────────────────────────────────────────


def _mock_popen(pid: int = 12345, poll_return=None):
    p = MagicMock()
    p.pid = pid
    p.poll.return_value = poll_return  # None = still running
    return p


def test_start_bridge_spawns_node_with_env(monkeypatch, tmp_path):
    import whatsapp_supervisor, db
    user_id = _make_user(1)

    captured_calls = []
    def fake_popen(args, env=None, stdout=None, stderr=None, cwd=None, start_new_session=False):
        captured_calls.append({"args": args, "env": env, "cwd": cwd})
        return _mock_popen()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())

    port = whatsapp_supervisor.start_bridge(user_id)
    assert port == 3030
    assert captured_calls[0]["env"]["BRIDGE_PORT"] == "3030"
    assert "wa_auth" in captured_calls[0]["env"]["WA_AUTH_DIR"]

    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        assert row.port == 3030
        assert row.status == "qr_pending"
        assert row.last_started_at is not None


def test_start_bridge_assigns_unique_port_per_user(monkeypatch):
    import whatsapp_supervisor, db
    a, b = _make_user(1), _make_user(2)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: _mock_popen())
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())

    pa = whatsapp_supervisor.start_bridge(a)
    pb = whatsapp_supervisor.start_bridge(b)
    assert pa != pb

    with db.session_scope() as s:
        ports = {r.user_id: r.port for r in s.query(db.WhatsAppBridge).all()}
    assert ports[a] != ports[b]


def test_start_bridge_idempotent_when_process_alive(monkeypatch):
    import whatsapp_supervisor
    user_id = _make_user(1)
    live = _mock_popen(pid=99, poll_return=None)

    call_count = 0
    def popen(*a, **k):
        nonlocal call_count
        call_count += 1
        return live

    monkeypatch.setattr("subprocess.Popen", popen)
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())

    whatsapp_supervisor.start_bridge(user_id)
    whatsapp_supervisor.start_bridge(user_id)
    assert call_count == 1


def test_start_bridge_passes_absolute_auth_dir_in_env(monkeypatch):
    """auth_dir in DB is relative; the bridge subprocess CWD differs from the
    Python supervisor's CWD, so a relative WA_AUTH_DIR points to a different
    folder than the supervisor wipes. start_bridge must resolve to absolute
    before exporting to env."""
    import whatsapp_supervisor
    user_id = _make_user(1)
    captured_env = {}

    def popen(cmd, env=None, **kw):
        captured_env.update(env or {})
        return _mock_popen(pid=42, poll_return=None)

    monkeypatch.setattr("subprocess.Popen", popen)
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())

    whatsapp_supervisor.start_bridge(user_id)
    assert "WA_AUTH_DIR" in captured_env
    from pathlib import Path
    assert Path(captured_env["WA_AUTH_DIR"]).is_absolute(), \
        f"WA_AUTH_DIR was relative: {captured_env['WA_AUTH_DIR']!r}"


def test_start_bridge_respawns_after_dead_process(monkeypatch):
    import whatsapp_supervisor
    user_id = _make_user(1)

    dead = _mock_popen(pid=1, poll_return=1)   # already exited
    alive = _mock_popen(pid=2, poll_return=None)

    returns = [dead, alive]
    def popen(*a, **k):
        return returns.pop(0)

    monkeypatch.setattr("subprocess.Popen", popen)
    monkeypatch.setattr("builtins.open", lambda *a, **k: MagicMock())

    whatsapp_supervisor.start_bridge(user_id)
    whatsapp_supervisor.start_bridge(user_id)
    assert not returns  # both popen calls happened


# ─── stop_bridge ──────────────────────────────────────────────────────────────


def test_stop_bridge_signals_and_clears_registry(monkeypatch):
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    proc = _mock_popen()
    whatsapp_supervisor._processes[user_id] = proc

    with db.session_scope() as s:
        db._SessionLocal = db._SessionLocal  # ensure engine live
        s.add(db.WhatsAppBridge(user_id=user_id, port=3030, auth_dir="x", status="running"))

    killed = []
    def fake_killpg(pgid, sig):
        killed.append((pgid, sig))
    monkeypatch.setattr("os.killpg", fake_killpg)
    monkeypatch.setattr("os.getpgid", lambda pid: pid)
    proc.wait.return_value = None

    ok = whatsapp_supervisor.stop_bridge(user_id, grace_seconds=0.1)
    assert ok is True
    assert killed[0][1] == 2 or killed[0][1] == 15  # SIGINT or SIGTERM
    assert user_id not in whatsapp_supervisor._processes
    with db.session_scope() as s:
        row = s.get(db.WhatsAppBridge, user_id)
        assert row.status == "stopped"


def test_stop_bridge_when_nothing_running_is_noop():
    import whatsapp_supervisor
    user_id = _make_user(1)
    assert whatsapp_supervisor.stop_bridge(user_id) is True


# ─── is_running ──────────────────────────────────────────────────────────────


def test_is_running_reflects_process_state():
    import whatsapp_supervisor
    user_id = _make_user(1)
    assert whatsapp_supervisor.is_running(user_id) is False
    whatsapp_supervisor._processes[user_id] = _mock_popen(poll_return=None)
    assert whatsapp_supervisor.is_running(user_id) is True
    whatsapp_supervisor._processes[user_id] = _mock_popen(poll_return=1)
    assert whatsapp_supervisor.is_running(user_id) is False


# ─── get_qr ───────────────────────────────────────────────────────────────────


def test_get_qr_returns_string_when_bridge_has_qr(monkeypatch):
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3030, auth_dir="x", status="qr_pending"))

    status = MagicMock()
    status.json.return_value = {"ready": False, "has_qr": True}
    qr = MagicMock()
    qr.json.return_value = {"qr": "ABCD1234"}
    monkeypatch.setattr("requests.get", lambda url, timeout=3: status if "/status" in url else qr)

    out = whatsapp_supervisor.get_qr(user_id, timeout_seconds=1)
    assert out == "ABCD1234"


def test_get_qr_returns_none_when_ready(monkeypatch):
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3030, auth_dir="x", status="running"))

    status = MagicMock()
    status.json.return_value = {"ready": True, "has_qr": False}
    monkeypatch.setattr("requests.get", lambda *a, **k: status)
    assert whatsapp_supervisor.get_qr(user_id, timeout_seconds=1) is None


def test_get_qr_returns_none_on_missing_row():
    import whatsapp_supervisor
    assert whatsapp_supervisor.get_qr(9999, timeout_seconds=0.1) is None


# ─── restore_running_bridges ─────────────────────────────────────────────────


def test_restore_running_bridges_only_picks_active(monkeypatch):
    import whatsapp_supervisor, db
    a, b, c = _make_user(1), _make_user(2), _make_user(3)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=a, port=3030, auth_dir="x", status="running"))
        s.add(db.WhatsAppBridge(user_id=b, port=3031, auth_dir="x", status="qr_pending"))
        s.add(db.WhatsAppBridge(user_id=c, port=3032, auth_dir="x", status="stopped"))

    calls = []
    def fake_start(uid):
        calls.append(uid)
        return 3030
    monkeypatch.setattr(whatsapp_supervisor, "start_bridge", fake_start)

    n = whatsapp_supervisor.restore_running_bridges()
    assert n == 2
    assert set(calls) == {a, b}  # c was stopped, skipped


def test_mark_running_flips_status():
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3030, auth_dir="x", status="qr_pending"))
    whatsapp_supervisor.mark_running(user_id)
    with db.session_scope() as s:
        assert s.get(db.WhatsAppBridge, user_id).status == "running"


# ─── pair-code idempotency ────────────────────────────────────────────────────


def test_request_pairing_code_reuses_existing_live_code(monkeypatch):
    """If pair-mode bridge already runs and /pair returns 200 with a code,
    don't cold-restart — that would invalidate the code the user is typing."""
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3031, auth_dir="x", status="qr_pending"))
    # Fake live bridge process
    whatsapp_supervisor._processes[user_id] = _mock_popen(pid=99, poll_return=None)

    pair_resp = MagicMock()
    pair_resp.status_code = 200
    pair_resp.json.return_value = {"code": "WXYZ-9999"}

    with patch("requests.post", return_value=pair_resp) as mock_post, \
         patch.object(whatsapp_supervisor, "start_bridge") as mock_start:
        result = whatsapp_supervisor.request_pairing_code(user_id, "972500000000")

    assert result == {"ok": True, "code": "WXYZ-9999"}
    mock_start.assert_not_called()
    mock_post.assert_called_once()


def test_request_pairing_code_cold_starts_when_no_live_bridge(monkeypatch):
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3031, auth_dir="x", status="stopped"))
    # No process registered — supervisor should cold-start.
    whatsapp_supervisor._processes.pop(user_id, None)

    pair_resp = MagicMock()
    pair_resp.status_code = 200
    pair_resp.json.return_value = {"code": "ABCD-1234"}

    with patch("requests.post", return_value=pair_resp), \
         patch.object(whatsapp_supervisor, "start_bridge", return_value=3031) as mock_start:
        result = whatsapp_supervisor.request_pairing_code(user_id, "972500000000")

    assert result == {"ok": True, "code": "ABCD-1234"}
    mock_start.assert_called_once_with(user_id, pair_phone="972500000000")


def test_request_pairing_code_already_paired_does_not_restart():
    """If WA already considers this device paired, return that — never
    cold-restart, since that would wipe the working session."""
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3031, auth_dir="x", status="running"))
    whatsapp_supervisor._processes[user_id] = _mock_popen(pid=99, poll_return=None)

    pair_resp = MagicMock()
    pair_resp.status_code = 409

    with patch("requests.post", return_value=pair_resp), \
         patch.object(whatsapp_supervisor, "start_bridge") as mock_start:
        result = whatsapp_supervisor.request_pairing_code(user_id, "972500000000")

    assert result["ok"] is False
    assert result.get("already_paired") is True
    mock_start.assert_not_called()


# ─── disable_for_user (cleanup on access loss) ────────────────────────────────


def test_disable_for_user_stops_running_bridge():
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3031, auth_dir="x", status="running"))
    with patch.object(whatsapp_supervisor, "stop_bridge", return_value=True) as mock_stop:
        assert whatsapp_supervisor.disable_for_user(user_id) is True
        mock_stop.assert_called_once_with(user_id)


def test_disable_for_user_skips_external_bridge():
    """External bridges (Daria's legacy single-user setup) must not be touched."""
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3030, auth_dir="x", status="external"))
    with patch.object(whatsapp_supervisor, "stop_bridge") as mock_stop:
        assert whatsapp_supervisor.disable_for_user(user_id) is False
        mock_stop.assert_not_called()
    with db.session_scope() as s:
        assert s.get(db.WhatsAppBridge, user_id).status == "external"


def test_disable_for_user_noop_when_no_row():
    import whatsapp_supervisor
    user_id = _make_user(1)
    with patch.object(whatsapp_supervisor, "stop_bridge") as mock_stop:
        assert whatsapp_supervisor.disable_for_user(user_id) is False
        mock_stop.assert_not_called()


def test_disable_for_user_swallows_stop_errors():
    import whatsapp_supervisor, db
    user_id = _make_user(1)
    with db.session_scope() as s:
        s.add(db.WhatsAppBridge(user_id=user_id, port=3031, auth_dir="x", status="running"))
    with patch.object(whatsapp_supervisor, "stop_bridge", side_effect=RuntimeError("boom")):
        # Must not raise — webhook handler relies on this not crashing.
        assert whatsapp_supervisor.disable_for_user(user_id) is False
