"""SQLAlchemy ORM models for all ANE entities."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, relationship


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


# ── User ─────────────────────────────────────────────────

class User(Base):
    """Registered user account."""
    __tablename__ = "users"

    id            = Column(String, primary_key=True, default=_new_id)
    username      = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    display_name  = Column(String, default="")
    is_adult      = Column(Boolean, default=False)  # 成年标记，HO 权限
    created_at    = Column(DateTime, default=datetime.utcnow)
    is_active     = Column(Boolean, default=True)

    sessions = relationship("WorldSession", back_populates="user", cascade="all, delete-orphan")


# ── WorldSession ─────────────────────────────────────────────

class WorldSession(Base):
    __tablename__ = "sessions"

    id          = Column(String, primary_key=True, default=_new_id)
    user_id     = Column(String, ForeignKey("users.id"), nullable=False)
    name        = Column(String, default="未命名世界")
    world_time  = Column(String, default="第1年·1月·1日·春·清晨")  # human-readable time label
    time_epoch  = Column(Integer, default=0)               # internal tick counter
    created_at  = Column(DateTime, default=datetime.utcnow)
    is_active   = Column(Boolean, default=True)
    map_data    = Column(JSON, default=None)               # world map: {seed, count, locations[{x,y,name}]}
    world_intro = Column(Text, default="")                 # world intro text shown after map save

    # Relationships
    user    = relationship("User", back_populates="sessions")
    player  = relationship("Player", back_populates="session", uselist=False, cascade="all, delete-orphan")
    npcs    = relationship("NPC", back_populates="session", cascade="all, delete-orphan")
    regions = relationship("WorldRegion", back_populates="session", cascade="all, delete-orphan")
    events  = relationship("EventLog", back_populates="session", cascade="all, delete-orphan")
    facts   = relationship("Fact", back_populates="session", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="session", cascade="all, delete-orphan")
    npc_relationships = relationship("NPC_Relationship", back_populates="session", cascade="all, delete-orphan")


# ── Player ───────────────────────────────────────────────────

class Player(Base):
    __tablename__ = "players"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False, unique=True)
    name          = Column(String, default="无名修士")
    cultivation   = Column(String, default="凡人")           # e.g. 筑基初期
    location      = Column(String, default="青云山·山门")     # current location
    inventory     = Column(JSON, default=list)               # list of item dicts
    status        = Column(JSON, default=dict)               # freeform status flags
    long_term_abilities = Column(JSON, default=list)         # permanent abilities
    attributes    = Column(JSON, default=dict)               # HP/MP/attack etc.

    session = relationship("WorldSession", back_populates="player")


# ── NPC ──────────────────────────────────────────────────────

class NPC(Base):
    __tablename__ = "npcs"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    name          = Column(String, nullable=False)
    identity      = Column(String, default="散修")           # role / title
    appearance    = Column(Text, default="")                 # physical description
    personality   = Column(Text, default="")                 # personality traits
    cultivation   = Column(String, default="凡人")
    location      = Column(String, default="")
    relations     = Column(JSON, default=dict)               # {player_relation, affinity_score, ...}
    abilities     = Column(JSON, default=list)
    equipment     = Column(JSON, default=list)
    long_term_state  = Column(JSON, default=dict)            # persistent state
    short_term_state = Column(JSON, default=dict)            # temporary state (cleared on scene change)
    behavior      = Column(Text, default="")                 # current activity/behavior description
    is_core       = Column(Boolean, default=False)           # Core character: always in Active Set
    is_important  = Column(Boolean, default=False)           # Player-marked: full detail, permanent memory
    npc_type      = Column(String, default="named")          # "named" = persistent world character, "background" = scene-generated passerby
    gender        = Column(String, default="")               # "男" / "女"
    age           = Column(Integer, nullable=True)            # 年龄，None=未知
    is_alive      = Column(Boolean, default=True)

    session = relationship("WorldSession", back_populates="npcs")


# ── World / Region ───────────────────────────────────────────

class WorldRegion(Base):
    """A region, city, sect, or building — hierarchical via parent_id."""
    __tablename__ = "world_regions"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    name          = Column(String, nullable=False)
    region_type   = Column(String, default="area")           # area / city / sect / building / resource
    description   = Column(Text, default="")
    parent_id     = Column(String, ForeignKey("world_regions.id"), nullable=True)
    attributes    = Column(JSON, default=dict)               # type-specific data

    session = relationship("WorldSession", back_populates="regions")


# ── Event Log ────────────────────────────────────────────────

class EventLog(Base):
    """Persistent record of every state-changing event."""
    __tablename__ = "event_logs"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    event_type    = Column(String, nullable=False)           # QuestAccepted, Travel, Combat, ...
    timestamp     = Column(DateTime, default=datetime.utcnow)
    world_time    = Column(String, default="")               # world time when event occurred
    data          = Column(JSON, default=dict)               # event payload

    session = relationship("WorldSession", back_populates="events")


# ── Fact (permanent, never compressed) ────────────────────────

class Fact(Base):
    __tablename__ = "facts"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    content       = Column(Text, nullable=False)             # e.g. "林雨凝是玩家的道侣"
    category      = Column(String, default="general")        # character / world / quest / relationship
    priority      = Column(Integer, default=5)               # 1-10, higher = more important
    created_at    = Column(DateTime, default=datetime.utcnow)

    session = relationship("WorldSession", back_populates="facts")


# ── NPC Relationship Graph ──────────────────────────────────

class NPC_Relationship(Base):
    """A directed relationship edge between two NPCs (or NPC→player).
    source_id → target_id : type (e.g. 师徒, 夫妻, 仇敌) + description.
    """
    __tablename__ = "npc_relationships"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    source_id     = Column(String, ForeignKey("npcs.id"), nullable=True)   # None = created by narrative (no DB NPC yet)
    source_name   = Column(String, nullable=False)                         # always denormalized for display
    target_id     = Column(String, ForeignKey("npcs.id"), nullable=True)   # None = player or non-DB entity
    target_name   = Column(String, nullable=False)
    rel_type      = Column(String, nullable=False)                         # e.g. 师徒/夫妻/仇敌/恋人/朋友
    description   = Column(Text, default="")                               # free-form description of the relationship
    affinity      = Column(Integer, default=0)                             # -100 (hostile) to +100 (close)
    updated_at    = Column(DateTime, default=datetime.utcnow)

    session = relationship("WorldSession", back_populates="npc_relationships")


# ── Memory (Summary + Conversation) ──────────────────────────

class Memory(Base):
    """Stores conversation rounds and summaries."""
    __tablename__ = "memories"

    id            = Column(String, primary_key=True, default=_new_id)
    session_id    = Column(String, ForeignKey("sessions.id"), nullable=False)
    memory_type   = Column(String, nullable=False)           # "conversation" or "summary"
    content       = Column(Text, nullable=False)
    turn_number   = Column(Integer, default=0)               # which turn this belongs to
    created_at    = Column(DateTime, default=datetime.utcnow)

    session = relationship("WorldSession", back_populates="memories")
