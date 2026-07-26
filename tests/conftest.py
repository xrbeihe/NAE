"""Pytest configuration for async tests.

Ensures the 'ane' package can be found under backend/.
"""

import sys
from pathlib import Path

# Add backend/ to sys.path so tests can |import ane
_tests_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_tests_root.parent / "backend"))

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ane.database.models import Base

# Auto-detect async tests — no need for @pytest.mark.asyncio on every test
pytest_asyncio_mode = "auto"


@pytest_asyncio.fixture
async def engine():
    """In-memory SQLite engine for testing."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    """Session factory -> single session for a test."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
