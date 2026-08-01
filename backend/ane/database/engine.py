"""Async SQLAlchemy engine and session factory.

On startup, enables SQLite WAL mode so the database can handle
concurrent reads without locking. This means the dev server and
any background process can both access the DB safely.
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from ane.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_async_engine(DATABASE_URL, echo=False)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """Yield an async session (FastAPI dependency injection)."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables and enable WAL mode. Call once at app startup."""
    from ane.database.models import Base  # noqa: F401 — ensure models are registered

    async with engine.begin() as conn:
        # Enable WAL mode before creating tables — avoids
        # "database is locked" when multiple processes touch the DB.
        if "sqlite" in DATABASE_URL:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=3000"))
            logger.info("SQLite WAL mode enabled")
            # Non-destructive schema migration for pre-existing databases:
            # add worldview columns to sessions if missing. ALTER TABLE with
            # a NOT NULL DEFAULT is a metadata-only operation in SQLite — it
            # does not rewrite rows, so live data is preserved.
            cols = await conn.execute(text("PRAGMA table_info(sessions)"))
            col_names = {row[1] for row in cols.fetchall()}
            if col_names:
                if "worldview" not in col_names:
                    await conn.execute(
                        text("ALTER TABLE sessions ADD COLUMN worldview TEXT NOT NULL DEFAULT 'xianxia_v1'")
                    )
                    logger.info("Migration: added sessions.worldview (default 'xianxia_v1')")
                if "worldview_version" not in col_names:
                    await conn.execute(
                        text("ALTER TABLE sessions ADD COLUMN worldview_version TEXT NOT NULL DEFAULT ''")
                    )
                    logger.info("Migration: added sessions.worldview_version (default '')")
                if "timeline_id" not in col_names:
                    await conn.execute(
                        text("ALTER TABLE sessions ADD COLUMN timeline_id TEXT NOT NULL DEFAULT ''")
                    )
                    logger.info("Migration: added sessions.timeline_id (default '')")
        await conn.run_sync(Base.metadata.create_all)
