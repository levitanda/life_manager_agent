"""Phase H: groups + shared goals business logic.

Pure-Python functions (no Telegram coupling). The Telegram-facing
`/group_*` command handlers in `bot_handlers.py` delegate to these.

Errors:
- `LookupError` — group/user not found
- `PermissionError` — caller lacks admin privilege
- `ValueError` — bad input (duplicate name, name conflict, last-admin
  leave attempt, …)
- `InviteTokenError` — invite-link payload invalid or expired
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any, Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import db

logger = logging.getLogger(__name__)


# 7 days, matching the spec.
INVITE_TOKEN_MAX_AGE_SECONDS = 7 * 86400
_INVITE_SALT = "group-invite-v1"


class InviteTokenError(Exception):
    """Raised when verify_invite_token fails — invalid signature or expired."""


# ─── Token signing helpers ───────────────────────────────────────────────────


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("MASTER_KEY") or os.environ.get("STATE_SECRET") or "dev-secret"
    return URLSafeTimedSerializer(secret, salt=_INVITE_SALT)


def sign_invite_token(group_id: int, inviter_user_id: int) -> str:
    """Sign an invite token. Token encodes group_id and inviter_user_id."""
    return _serializer().dumps({
        "group_id": int(group_id),
        "inviter_user_id": int(inviter_user_id),
    })


def verify_invite_token(token: str) -> dict[str, int]:
    """Verify and decode a group-invite token. Raises InviteTokenError on
    bad/expired signature."""
    try:
        data = _serializer().loads(token, max_age=INVITE_TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired as e:
        raise InviteTokenError(f"expired: {e}") from e
    except BadSignature as e:
        raise InviteTokenError(f"invalid: {e}") from e
    try:
        return {
            "group_id": int(data["group_id"]),
            "inviter_user_id": int(data["inviter_user_id"]),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise InviteTokenError(f"malformed: {e}") from e


# ─── Internal lookups ────────────────────────────────────────────────────────


def _get_group_or_raise(session, group_id: int) -> db.Group:
    g = session.get(db.Group, group_id)
    if g is None:
        raise LookupError(f"group {group_id} not found")
    return g


def _get_member(session, group_id: int, user_id: int) -> Optional[db.GroupMember]:
    return (
        session.query(db.GroupMember)
        .filter_by(group_id=group_id, user_id=user_id)
        .one_or_none()
    )


def _require_admin(session, group_id: int, user_id: int) -> db.GroupMember:
    m = _get_member(session, group_id, user_id)
    if m is None or m.accepted_at is None:
        raise PermissionError(f"user {user_id} is not a member of group {group_id}")
    if m.role != "admin":
        raise PermissionError(f"user {user_id} is not admin of group {group_id}")
    return m


def _require_member(session, group_id: int, user_id: int) -> db.GroupMember:
    m = _get_member(session, group_id, user_id)
    if m is None or m.accepted_at is None:
        raise PermissionError(f"user {user_id} is not a member of group {group_id}")
    return m


def _count_admins(session, group_id: int) -> int:
    return (
        session.query(db.GroupMember)
        .filter(
            db.GroupMember.group_id == group_id,
            db.GroupMember.role == "admin",
            db.GroupMember.accepted_at.isnot(None),
        )
        .count()
    )


def _user_has_group_named(session, user_id: int, name: str) -> bool:
    """Return True if `user_id` is already a member of a group with this
    name (case-sensitive). Used to enforce per-user name uniqueness."""
    needle = name.strip()
    rows = (
        session.query(db.Group)
        .join(db.GroupMember, db.GroupMember.group_id == db.Group.id)
        .filter(db.GroupMember.user_id == user_id, db.Group.name == needle)
        .all()
    )
    return bool(rows)


# ─── Public API ──────────────────────────────────────────────────────────────


def create_group(creator_user_id: int, name: str) -> dict[str, Any]:
    """Create a new group with `creator_user_id` as admin. Returns
    `{group_id, name}`. Raises ValueError on empty/duplicate name."""
    if not name or not name.strip():
        raise ValueError("group name is required")
    name = name.strip()
    with db.session_scope() as s:
        if _user_has_group_named(s, creator_user_id, name):
            raise ValueError(f"you are already in a group named «{name}»")
        g = db.create_group(s, name=name, creator_user_id=creator_user_id)
        return {"group_id": g.id, "name": g.name}


def list_my_groups(user_id: int) -> list[dict[str, Any]]:
    """All groups the user is a member of (accepted invites only). Each
    entry: name, group_id, role, member_count, pending bool."""
    out: list[dict[str, Any]] = []
    with db.session_scope() as s:
        rows = (
            s.query(db.GroupMember, db.Group)
            .join(db.Group, db.Group.id == db.GroupMember.group_id)
            .filter(db.GroupMember.user_id == user_id)
            .all()
        )
        for member, group in rows:
            count = (
                s.query(db.GroupMember)
                .filter_by(group_id=group.id)
                .filter(db.GroupMember.accepted_at.isnot(None))
                .count()
            )
            out.append({
                "group_id": group.id,
                "name": group.name,
                "role": member.role,
                "member_count": count,
                "pending": member.accepted_at is None,
            })
    return out


def list_members(user_id: int, group_id: int) -> list[dict[str, Any]]:
    """List all members of a group. Caller must be a member."""
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        _require_member(s, group_id, user_id)
        rows = (
            s.query(db.GroupMember, db.User)
            .join(db.User, db.User.id == db.GroupMember.user_id)
            .filter(db.GroupMember.group_id == group_id)
            .all()
        )
        out = []
        for member, u in rows:
            out.append({
                "user_id": u.id,
                "username": u.telegram_username,
                "display_name": u.display_name,
                "role": member.role,
                "pending": member.accepted_at is None,
            })
        return out


def invite_user(admin_user_id: int, group_id: int, username: str) -> dict[str, Any]:
    """Invite by Telegram @username.

    If the invitee already exists in our DB → create a pending membership
    row and return `{user_id, pending: True}`. The caller (slash-command
    handler) then notifies the invitee in Telegram so they can /accept.

    If not found → return `{invite_token: <token>}` so the caller can
    build a deep-link the invitee opens with /start.
    """
    if not username or not username.strip():
        raise ValueError("username is required")
    needle = username.strip().lstrip("@")
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        target = db.get_user_by_telegram_username(s, needle)
        if target is None:
            token = sign_invite_token(group_id, admin_user_id)
            return {"invite_token": token, "pending": True}
        # Already a member?
        existing = _get_member(s, group_id, target.id)
        if existing is not None:
            if existing.accepted_at is not None:
                return {"user_id": target.id, "already_member": True}
            return {"user_id": target.id, "pending": True, "already_invited": True}
        s.add(db.GroupMember(
            group_id=group_id,
            user_id=target.id,
            role="member",
            accepted_at=None,
            invited_by=admin_user_id,
            joined_at=datetime.datetime.utcnow(),
        ))
        return {"user_id": target.id, "pending": True}


def accept_invite(user_id: int, group_id: int) -> None:
    """Fill the pending row's accepted_at. Raises LookupError if no row."""
    with db.session_scope() as s:
        m = _get_member(s, group_id, user_id)
        if m is None:
            raise LookupError("no pending invite for this user in that group")
        if m.accepted_at is not None:
            return  # idempotent
        m.accepted_at = datetime.datetime.utcnow()


def decline_invite(user_id: int, group_id: int) -> None:
    """Remove the pending row. Idempotent."""
    with db.session_scope() as s:
        m = _get_member(s, group_id, user_id)
        if m is None:
            return
        if m.accepted_at is not None:
            # Already accepted — declining post-accept is a leave; that's
            # what leave_group is for. Raise to keep the contract sharp.
            raise ValueError("cannot decline; already accepted (use leave)")
        s.delete(m)


def add_user_to_group_via_invite(user_id: int, group_id: int) -> str:
    """Used by the invite-link entrypoint in onboarding. Adds the user as
    a member directly (no pending state). Returns the group name.

    Idempotent — if the user is already a member, just returns the group
    name without modifying state.
    """
    with db.session_scope() as s:
        g = _get_group_or_raise(s, group_id)
        existing = _get_member(s, group_id, user_id)
        if existing is not None:
            if existing.accepted_at is None:
                existing.accepted_at = datetime.datetime.utcnow()
            return g.name
        s.add(db.GroupMember(
            group_id=group_id,
            user_id=user_id,
            role="member",
            accepted_at=datetime.datetime.utcnow(),
            joined_at=datetime.datetime.utcnow(),
        ))
        return g.name


def remove_member(admin_user_id: int, group_id: int, target_user_id: int) -> None:
    """Admin removes a member. Cannot remove self if last admin."""
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        target = _get_member(s, group_id, target_user_id)
        if target is None:
            raise LookupError("user is not in this group")
        if target_user_id == admin_user_id:
            # Self-removal — guard the last admin
            if target.role == "admin" and _count_admins(s, group_id) <= 1:
                raise ValueError("cannot remove the last admin from a non-empty group")
        s.delete(target)


def leave_group(user_id: int, group_id: int) -> None:
    """Leave a group. Fails if the caller is the last admin AND the group
    has other accepted members (caller must promote someone else first)."""
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        m = _get_member(s, group_id, user_id)
        if m is None:
            raise LookupError("you are not in this group")
        if m.role == "admin" and _count_admins(s, group_id) <= 1:
            other_accepted = (
                s.query(db.GroupMember)
                .filter(
                    db.GroupMember.group_id == group_id,
                    db.GroupMember.user_id != user_id,
                    db.GroupMember.accepted_at.isnot(None),
                )
                .count()
            )
            if other_accepted > 0:
                raise ValueError("you are the last admin; promote someone else first")
        s.delete(m)


def promote(admin_user_id: int, group_id: int, target_user_id: int) -> None:
    """Promote a member to admin."""
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        target = _get_member(s, group_id, target_user_id)
        if target is None:
            raise LookupError("user is not in this group")
        target.role = "admin"


def demote(admin_user_id: int, group_id: int, target_user_id: int) -> None:
    """Demote an admin to member. Fails if it would leave the group with 0 admins."""
    with db.session_scope() as s:
        _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        target = _get_member(s, group_id, target_user_id)
        if target is None:
            raise LookupError("user is not in this group")
        if target.role != "admin":
            raise ValueError("user is not an admin")
        if _count_admins(s, group_id) <= 1:
            raise ValueError("cannot demote the last admin")
        target.role = "member"


def rename(admin_user_id: int, group_id: int, new_name: str) -> None:
    """Rename a group."""
    if not new_name or not new_name.strip():
        raise ValueError("new name is required")
    new_name = new_name.strip()
    with db.session_scope() as s:
        g = _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        if g.name == new_name:
            return
        if _user_has_group_named(s, admin_user_id, new_name):
            raise ValueError(f"you are already in a group named «{new_name}»")
        g.name = new_name


def delete_group(admin_user_id: int, group_id: int) -> None:
    """Delete a group. Orphans shared goals (group_id → NULL, user_id stays
    as the original creator)."""
    with db.session_scope() as s:
        g = _get_group_or_raise(s, group_id)
        _require_admin(s, group_id, admin_user_id)
        # Orphan goals: drop the group_id pointer but keep the goal alive
        # under its original owner.
        shared_goals = (
            s.query(db.Goal)
            .filter(db.Goal.group_id == group_id)
            .all()
        )
        for goal in shared_goals:
            # Pick the surviving owner: the original creator referenced by
            # goal.user_id is the obvious choice — fall back to the earliest
            # goal_collaborators row of role='owner' if for some reason
            # goal.user_id is missing.
            if goal.user_id is None:
                first_owner = (
                    s.query(db.GoalCollaborator)
                    .filter(
                        db.GoalCollaborator.goal_id == goal.id,
                        db.GoalCollaborator.role == "owner",
                    )
                    .order_by(db.GoalCollaborator.invited_at.asc())
                    .first()
                )
                if first_owner is not None:
                    goal.user_id = first_owner.user_id
            goal.group_id = None
        # Clean any explicit goal_collaborators that referenced members of
        # this group via the shared goal relationship — but only the
        # ones whose goal had this group_id and the user isn't the
        # surviving owner. Keep the owner row.
        for goal in shared_goals:
            collabs = (
                s.query(db.GoalCollaborator)
                .filter(db.GoalCollaborator.goal_id == goal.id)
                .all()
            )
            for c in collabs:
                if c.user_id != goal.user_id:
                    s.delete(c)
        # GroupMember rows cascade via FK ondelete=CASCADE.
        s.delete(g)
