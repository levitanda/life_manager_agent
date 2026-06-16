"""SQLAlchemy ORM models + session management for multi-tenant life-agent.

Schema covers the relational core (auth, billing, integrations). Bulky
per-user content (diary, conversation history, scheduled actions, A2A
registry, WA group registry) stays as flat files under
`data/users/{user_id}/`.

Tokens and integration secrets are stored Fernet-encrypted in BLOB columns;
the master key comes from `MASTER_KEY` env. See `crypto.py` for the wrapper.
"""

from __future__ import annotations

import datetime
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


# ─── Config ──────────────────────────────────────────────────────────────────

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def db_path() -> str:
    return os.environ.get("DB_PATH", "data/app.db")


def data_dir() -> str:
    return os.environ.get("DATA_DIR", "data")


def _ensure_dirs() -> None:
    Path(data_dir()).mkdir(parents=True, exist_ok=True)
    Path(data_dir(), "users").mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _ensure_dirs()
        url = f"sqlite:///{db_path()}"
        _engine = create_engine(url, future=True)
        # Enable foreign keys for SQLite (off by default)
        from sqlalchemy import event
        @event.listens_for(_engine, "connect")
        def _enable_fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
        _SessionLocal = sessionmaker(
            bind=_engine, autocommit=False, autoflush=False, future=True
        )
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a SQLAlchemy session that commits on success and rolls back on error."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables. Idempotent."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _seed_promo_codes()


def _seed_promo_codes() -> None:
    """Insert built-in promo codes if missing."""
    seed = [("LEVITANONLY", "lifetime_free", None)]
    with session_scope() as s:
        for code, grants, max_red in seed:
            if not s.get(PromoCode, code):
                s.add(PromoCode(code=code, grants=grants, max_redemptions=max_red))


def reset_for_tests() -> None:
    """For tests: drop the cached engine so the next get_engine() picks up env changes."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


# ─── ORM Base ────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


# ─── Models ──────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True)
    telegram_user_id: int = Column(BigInteger, unique=True, nullable=False, index=True)
    telegram_chat_id: int = Column(BigInteger, nullable=False)
    display_name: Optional[str] = Column(String)
    timezone: str = Column(String, default="Europe/Moscow", nullable=False)
    morning_time: str = Column(String, default="06:30", nullable=False)
    evening_time: str = Column(String, default="21:30", nullable=False)
    subscription_status: str = Column(
        String, default="inactive", nullable=False
    )  # inactive | active | promo | cancelled | past_due
    stripe_customer_id: Optional[str] = Column(String)
    stripe_subscription_id: Optional[str] = Column(String)
    trial_ends_at: Optional[datetime.datetime] = Column(DateTime)
    created_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    # ─── Personalization (Phase C) ──────────────────────────────────────────
    city: Optional[str] = Column(Text)
    language: str = Column(Text, default="ru")
    news_country: Optional[str] = Column(Text)
    onboarding_state: str = Column(Text, default="pending")
    # Phase H: lower-cased Telegram @handle stored at signup so /group_invite
    # can resolve invitees by their public username.
    telegram_username: Optional[str] = Column(Text, index=True)

    google_token = relationship(
        "GoogleToken", uselist=False, cascade="all, delete-orphan", backref="user"
    )
    integrations = relationship(
        "UserIntegration", cascade="all, delete-orphan", backref="user"
    )
    whatsapp_bridge = relationship(
        "WhatsAppBridge", uselist=False, cascade="all, delete-orphan", backref="user"
    )

    @property
    def data_dir(self) -> Path:
        """Per-user data directory: data/users/{id}/."""
        p = Path(data_dir(), "users", str(self.id))
        p.mkdir(parents=True, exist_ok=True)
        return p

    def has_access(self) -> bool:
        return self.subscription_status in ("active", "promo")


class GoogleToken(Base):
    __tablename__ = "google_tokens"

    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_json_encrypted: bytes = Column(LargeBinary, nullable=False)
    scopes: str = Column(String, nullable=False)
    refreshed_at: Optional[datetime.datetime] = Column(DateTime)


class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "integration", name="uq_user_integration"),
    )

    id: int = Column(Integer, primary_key=True)
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration: str = Column(
        String, nullable=False
    )  # whatsapp | pushover | alice | tuya | vesync | diary_doc
    enabled: int = Column(Integer, default=0, nullable=False)
    config_json_encrypted: Optional[bytes] = Column(LargeBinary)


class WhatsAppBridge(Base):
    __tablename__ = "whatsapp_bridges"

    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    port: int = Column(Integer, unique=True, nullable=False)
    auth_dir: str = Column(String, nullable=False)
    status: str = Column(
        String, default="stopped", nullable=False
    )  # stopped | qr_pending | running | crashed
    last_started_at: Optional[datetime.datetime] = Column(DateTime)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    code: str = Column(String, primary_key=True)
    grants: str = Column(String, nullable=False)  # lifetime_free | 30_days_free
    redeemed_count: int = Column(Integer, default=0, nullable=False)
    max_redemptions: Optional[int] = Column(Integer)


# ─── Phase C: personalization, goals, groups ────────────────────────────────


class UserNewsFeed(Base):
    __tablename__ = "user_news_feeds"

    id: int = Column(Integer, primary_key=True)
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_name: str = Column(Text, nullable=False)
    url: str = Column(Text, nullable=False)
    enabled: int = Column(Integer, default=1, nullable=False)
    created_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )


# Groups must be declared before Goals (Goal.group_id → groups.id).
class Group(Base):
    __tablename__ = "groups"

    id: int = Column(Integer, primary_key=True)
    name: str = Column(Text, nullable=False)
    created_by: Optional[int] = Column(Integer, ForeignKey("users.id"))
    created_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: int = Column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: str = Column(Text, default="member", nullable=False)  # 'admin' | 'member'
    joined_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )
    # Phase H: NULL while an invite is pending; filled when the invitee accepts.
    accepted_at: Optional[datetime.datetime] = Column(DateTime)
    invited_by: Optional[int] = Column(Integer, ForeignKey("users.id"))


class Goal(Base):
    __tablename__ = "goals"

    id: int = Column(Integer, primary_key=True)
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Optional[int] = Column(Integer, ForeignKey("groups.id"), index=True)
    title: str = Column(Text, nullable=False)
    description: Optional[str] = Column(Text)
    category: Optional[str] = Column(Text)
    target_date: Optional[datetime.date] = Column(Date)
    created_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )
    completed_at: Optional[datetime.datetime] = Column(DateTime)
    status: str = Column(Text, default="active")


class GoalProgress(Base):
    __tablename__ = "goal_progress"

    id: int = Column(Integer, primary_key=True)
    goal_id: int = Column(
        Integer, ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    ts: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )
    note: Optional[str] = Column(Text)
    pct: Optional[int] = Column(Integer)


class GoalCollaborator(Base):
    __tablename__ = "goal_collaborators"

    goal_id: int = Column(
        Integer, ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: int = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: str = Column(Text, nullable=False)  # 'owner' | 'collaborator' | 'viewer'
    invited_by: Optional[int] = Column(Integer, ForeignKey("users.id"))
    invited_at: datetime.datetime = Column(
        DateTime, default=datetime.datetime.utcnow
    )
    accepted_at: Optional[datetime.datetime] = Column(DateTime)


# ─── Convenience accessors ───────────────────────────────────────────────────


def get_user_by_telegram_id(session: Session, telegram_user_id: int) -> Optional[User]:
    return (
        session.query(User)
        .filter(User.telegram_user_id == telegram_user_id)
        .one_or_none()
    )


def create_user(
    session: Session,
    telegram_user_id: int,
    telegram_chat_id: int,
    display_name: Optional[str] = None,
    timezone: str = "Europe/Moscow",
    telegram_username: Optional[str] = None,
) -> User:
    user = User(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        display_name=display_name,
        timezone=timezone,
        telegram_username=(telegram_username or "").lstrip("@").lower() or None,
    )
    session.add(user)
    session.flush()
    return user


def get_user_by_telegram_username(session: Session, username: str) -> Optional[User]:
    """Resolve a user by Telegram @handle (case-insensitive, leading @ stripped).
    Returns None if no such user is in the DB."""
    if not username:
        return None
    needle = username.lstrip("@").strip().lower()
    if not needle:
        return None
    return (
        session.query(User)
        .filter(User.telegram_username == needle)
        .one_or_none()
    )


def create_goal(
    session: Session,
    user_id: int,
    title: str,
    *,
    description: Optional[str] = None,
    category: Optional[str] = None,
    target_date: Optional[datetime.date] = None,
    group_id: Optional[int] = None,
) -> Goal:
    """Create a goal and the implicit owner row in goal_collaborators."""
    goal = Goal(
        user_id=user_id,
        title=title,
        description=description,
        category=category,
        target_date=target_date,
        group_id=group_id,
    )
    session.add(goal)
    session.flush()
    session.add(GoalCollaborator(
        goal_id=goal.id,
        user_id=user_id,
        role="owner",
        invited_by=user_id,
        accepted_at=datetime.datetime.utcnow(),
    ))
    session.flush()
    return goal


def create_group(
    session: Session,
    name: str,
    creator_id: Optional[int] = None,
    *,
    creator_user_id: Optional[int] = None,
) -> Group:
    """Create a group and add the creator as admin member.

    Accepts either positional `creator_id` (legacy) or kw-only
    `creator_user_id` (Phase H) — they mean the same thing.
    """
    cid = creator_user_id if creator_user_id is not None else creator_id
    if cid is None:
        raise ValueError("creator_user_id is required")
    group = Group(name=name, created_by=cid)
    session.add(group)
    session.flush()
    session.add(GroupMember(
        group_id=group.id,
        user_id=cid,
        role="admin",
        accepted_at=datetime.datetime.utcnow(),
        invited_by=cid,
    ))
    session.flush()
    return group


def redeem_promo(session: Session, user: User, code: str) -> tuple[bool, str]:
    """Try to redeem a promo code for a user. Returns (success, message)."""
    promo = session.get(PromoCode, code.upper().strip())
    if not promo:
        return False, f"Промокод «{code}» не найден."
    if promo.max_redemptions is not None and promo.redeemed_count >= promo.max_redemptions:
        return False, f"Промокод «{code}» больше не действует (лимит исчерпан)."
    if user.subscription_status == "promo":
        return False, "У тебя уже активен промокод."
    promo.redeemed_count += 1
    if promo.grants == "lifetime_free":
        user.subscription_status = "promo"
    elif promo.grants == "30_days_free":
        user.subscription_status = "promo"
        user.trial_ends_at = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    return True, f"Промокод «{code}» применён ✅"
