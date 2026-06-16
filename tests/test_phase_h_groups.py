"""Tests for Phase H: groups + shared goals + /group_* commands + tools."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_user(tg=100, *, name="Person", username=None):
    import db
    with db.session_scope() as s:
        u = db.create_user(
            s,
            telegram_user_id=tg,
            telegram_chat_id=tg,
            display_name=name,
            telegram_username=username,
        )
        u.subscription_status = "promo"
        u.timezone = "Europe/Moscow"
        u.language = "ru"
        return u.id


# ─── Pure-Python business logic ──────────────────────────────────────────────


def test_create_group_makes_creator_admin():
    user_id = _mk_user(100, name="A")
    import groups, db
    res = groups.create_group(user_id, "Семья")
    assert res["name"] == "Семья"
    assert isinstance(res["group_id"], int)
    with db.session_scope() as s:
        m = s.query(db.GroupMember).filter_by(
            group_id=res["group_id"], user_id=user_id
        ).one()
        assert m.role == "admin"
        assert m.accepted_at is not None


def test_create_group_blocks_duplicate_name_for_same_user():
    user_id = _mk_user(100)
    import groups
    groups.create_group(user_id, "Семья")
    with pytest.raises(ValueError):
        groups.create_group(user_id, "Семья")


def test_invite_existing_user_creates_pending_row():
    a = _mk_user(100, name="A", username="alice")
    b = _mk_user(101, name="B", username="bob")
    import groups, db
    res = groups.create_group(a, "Семья")
    gid = res["group_id"]
    invite = groups.invite_user(a, gid, "bob")
    assert invite["pending"] is True
    assert invite["user_id"] == b
    with db.session_scope() as s:
        m = s.query(db.GroupMember).filter_by(group_id=gid, user_id=b).one()
        assert m.accepted_at is None
        assert m.role == "member"
        assert m.invited_by == a


def test_accept_invite_fills_timestamp():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups, db
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    with db.session_scope() as s:
        m = s.query(db.GroupMember).filter_by(group_id=gid, user_id=b).one()
        assert m.accepted_at is not None


def test_decline_invite_removes_row():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups, db
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.decline_invite(b, gid)
    with db.session_scope() as s:
        m = s.query(db.GroupMember).filter_by(group_id=gid, user_id=b).one_or_none()
        assert m is None


def test_invite_unknown_username_returns_token():
    a = _mk_user(100, username="alice")
    import groups
    gid = groups.create_group(a, "Fam")["group_id"]
    res = groups.invite_user(a, gid, "nobody_special")
    assert "invite_token" in res
    payload = groups.verify_invite_token(res["invite_token"])
    assert payload["group_id"] == gid
    assert payload["inviter_user_id"] == a


def test_shared_goal_visible_to_member():
    """Create group with users A & B, create goal with group_id, B's
    dashboard._user_goals(B.id) includes it."""
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups, db, dashboard
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    with db.session_scope() as s:
        db.create_goal(s, user_id=a, title="Family fitness", group_id=gid)
    out = dashboard._user_goals(b)
    titles = {g["title"] for g in out}
    assert "Family fitness" in titles


def test_remove_member_requires_admin():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    c = _mk_user(102, username="carol")
    import groups
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    groups.invite_user(a, gid, "carol")
    groups.accept_invite(c, gid)
    # Non-admin B trying to remove C
    with pytest.raises(PermissionError):
        groups.remove_member(b, gid, c)


def test_leave_last_admin_blocks():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    # A is the sole admin, group has another member → can't leave
    with pytest.raises(ValueError):
        groups.leave_group(a, gid)


def test_leave_solo_admin_when_no_other_members_ok():
    a = _mk_user(100, username="alice")
    import groups
    gid = groups.create_group(a, "Solo")["group_id"]
    # No other members; leaving is fine.
    groups.leave_group(a, gid)


def test_promote_then_leave_works():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    groups.promote(a, gid, b)
    groups.leave_group(a, gid)  # B is admin now, so A can leave


def test_demote_last_admin_blocks():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    with pytest.raises(ValueError):
        groups.demote(a, gid, a)


def test_rename_blocks_duplicate_per_user():
    a = _mk_user(100)
    import groups
    g1 = groups.create_group(a, "Family")["group_id"]
    groups.create_group(a, "Work")
    with pytest.raises(ValueError):
        groups.rename(a, g1, "Work")


def test_delete_group_orphans_shared_goals():
    """goal.group_id becomes NULL, goal.user_id stays as creator."""
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import groups, db
    gid = groups.create_group(a, "Fam")["group_id"]
    groups.invite_user(a, gid, "bob")
    groups.accept_invite(b, gid)
    with db.session_scope() as s:
        goal = db.create_goal(s, user_id=a, title="Family trip", group_id=gid)
        goal_id = goal.id
    groups.delete_group(a, gid)
    with db.session_scope() as s:
        g = s.get(db.Goal, goal_id)
        assert g is not None
        assert g.group_id is None
        assert g.user_id == a


# ─── Invite token signing / verification ─────────────────────────────────────


def test_invite_token_roundtrip_signs_and_verifies():
    import groups
    token = groups.sign_invite_token(group_id=42, inviter_user_id=7)
    decoded = groups.verify_invite_token(token)
    assert decoded == {"group_id": 42, "inviter_user_id": 7}


def test_invite_token_expired_rejected(monkeypatch):
    """Force the verifier's max_age to 0 so any non-zero clock gap rejects."""
    import groups
    token = groups.sign_invite_token(group_id=42, inviter_user_id=7)
    monkeypatch.setattr(groups, "INVITE_TOKEN_MAX_AGE_SECONDS", -1)
    with pytest.raises(groups.InviteTokenError):
        groups.verify_invite_token(token)


def test_invite_token_garbage_rejected():
    import groups
    with pytest.raises(groups.InviteTokenError):
        groups.verify_invite_token("not-a-real-token")


# ─── /group_invite slash command behavior ────────────────────────────────────


def _mk_update(tg_id: int, args: list[str] | None = None):
    upd = MagicMock()
    upd.effective_user.id = tg_id
    upd.effective_user.first_name = f"User{tg_id}"
    upd.effective_user.username = f"user_{tg_id}"
    upd.effective_chat.id = tg_id
    upd.effective_message.reply_text = AsyncMock()
    upd.message = upd.effective_message
    return upd


def _mk_ctx(args=None, bot_username="lifebot"):
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.username = bot_username
    ctx.bot.send_message = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_cmd_group_invite_username_not_in_db_returns_link():
    a = _mk_user(100, username="alice")
    import bot_handlers, groups
    # Admin needs a real Group to invite to
    gid = groups.create_group(a, "Fam")["group_id"]
    upd = _mk_update(100, args=[str(gid), "ghost_user"])
    ctx = _mk_ctx(args=[str(gid), "ghost_user"], bot_username="mybotname")
    with patch.object(bot_handlers, "_authorize", new=AsyncMock(return_value=(None, a))):
        await bot_handlers.cmd_group_invite(upd, ctx)
    sent = upd.message.reply_text.call_args[0][0]
    assert "t.me/mybotname" in sent or "https://t.me/mybotname" in sent
    assert "start=invite_" in sent


@pytest.mark.asyncio
async def test_cmd_group_invite_existing_user_dms_them():
    a = _mk_user(100, username="alice")
    b = _mk_user(101, username="bob")
    import bot_handlers, groups
    gid = groups.create_group(a, "Fam")["group_id"]
    upd = _mk_update(100, args=[str(gid), "@bob"])
    ctx = _mk_ctx(args=[str(gid), "@bob"], bot_username="mybot")
    with patch.object(bot_handlers, "_authorize", new=AsyncMock(return_value=(None, a))):
        await bot_handlers.cmd_group_invite(upd, ctx)
    # DM to B should have happened
    ctx.bot.send_message.assert_awaited()
    target_chat = ctx.bot.send_message.call_args.kwargs.get("chat_id")
    assert target_chat == 101


# ─── Onboarding invite-token entrypoint ───────────────────────────────────────


@pytest.mark.asyncio
async def test_onboarding_invite_token_adds_to_group():
    a = _mk_user(100, username="alice")
    import groups, onboarding, db
    gid = groups.create_group(a, "Fam")["group_id"]
    token = groups.sign_invite_token(group_id=gid, inviter_user_id=a)

    upd = _mk_update(200)  # new tg user
    upd.effective_user.username = "bob"
    ctx = _mk_ctx(args=[f"invite_{token}"])
    await onboarding.cmd_start(upd, ctx)
    # The new user should now be a member of the group with accepted_at set
    with db.session_scope() as s:
        new_user = db.get_user_by_telegram_id(s, 200)
        assert new_user is not None
        m = s.query(db.GroupMember).filter_by(group_id=gid, user_id=new_user.id).one()
        assert m.accepted_at is not None


# ─── Tools ────────────────────────────────────────────────────────────────────


def test_tool_create_group_persists():
    a = _mk_user(100)
    import tools, db
    r = tools.create_group(name="MyTeam", _user_id=a)
    assert r["status"] == "ok"
    with db.session_scope() as s:
        names = [g.name for g in s.query(db.Group).all()]
    assert "MyTeam" in names


def test_tool_add_shared_goal_persists():
    a = _mk_user(100)
    import tools, groups, db
    groups.create_group(a, "Squad")
    r = tools.add_shared_goal(group_name="squad", title="Group fitness", _user_id=a)
    assert r["status"] == "ok"
    with db.session_scope() as s:
        rows = [(g.title, g.group_id) for g in s.query(db.Goal).filter_by(user_id=a).all()]
    assert len(rows) == 1
    assert rows[0][0] == "Group fitness"
    assert rows[0][1] is not None


def test_tool_list_my_groups_returns_groups():
    a = _mk_user(100)
    import tools, groups
    groups.create_group(a, "Alpha")
    groups.create_group(a, "Beta")
    r = tools.list_my_groups(_user_id=a)
    assert r["status"] == "ok"
    assert "Alpha" in r["summary"]
    assert "Beta" in r["summary"]


def test_tool_add_shared_goal_unknown_group_errors():
    a = _mk_user(100)
    import tools
    r = tools.add_shared_goal(group_name="nope", title="x", _user_id=a)
    assert r["status"] == "error"
