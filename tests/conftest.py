"""Pytest configuration for async tests.

Ensures the 'ane' package can be found under backend/.
"""

import gc
import sys
from pathlib import Path

# Add backend/ to sys.path so tests can |import ane
_tests_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_tests_root.parent / "backend"))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ane.database.models import Base

# Auto-detect async tests — no need for @pytest.mark.asyncio on every test
pytest_asyncio_mode = "auto"


def _stop_lingering_aiosqlite_connections() -> int:
    """兜底停掉还活着的 aiosqlite 连接工作线程，返回停掉的个数。

    背景：aiosqlite 每个连接会起一个 **非守护线程**（`Thread(target=_connection_worker_thread)`，
    没有 daemon=True）。Python 退出时会 join 所有非守护线程，所以只要有一个连接没关闭，
    解释器就会永久卡在 `threading._shutdown()`——现象是 pytest 打印完 "N passed" 后不退出。

    本仓库最容易漏关的是 turn 提交后 fire-and-forget 的后台 llm_summary 任务：它用全局
    session 工厂（`ane.database.engine.async_session_factory`）另开 session，测试跑完时它
    可能还攥着连接。这里先 dispose 全局引擎，再直接给残留连接的工作线程发停止哨兵
    （`Connection.stop()` 不需要事件循环，因此不会踩"future 属于另一个 loop"的坑）。
    """
    import aiosqlite

    stopped = 0
    for obj in gc.get_objects():
        if not isinstance(obj, aiosqlite.Connection):
            continue
        thread = getattr(obj, "_thread", None)
        if thread is not None and thread.is_alive():
            try:
                obj.stop()          # 往工作线程队列放哨兵 → 线程随后退出
                stopped += 1
            except Exception:
                pass
    return stopped


@pytest.fixture(scope="session", autouse=True)
def _no_lingering_aiosqlite_threads():
    """会话结束时停掉残留的 aiosqlite 连接线程，避免解释器退出时挂死。

    现象：pytest 打印完 "N passed" 后进程不退出（CI 里表现为 job 一直挂着）。
    原因：aiosqlite 每个连接起一个 **非守护** 工作线程，Python 退出时
    `threading._shutdown()` 会 join 所有非守护线程；只要有一个连接没被关闭就永久等待。
    本仓库最容易漏关的是 turn 提交后 fire-and-forget 的后台 llm_summary——它用全局
    session 工厂（`ane.database.engine.async_session_factory`）另开 session，测试跑完时
    可能还攥着连接。`Connection.stop()` 会把"关连接 + 停线程"投给工作线程且不需要
    事件循环，所以在会话结束时调用是安全的（此时已没有测试在等它的 future）。
    """
    yield
    stopped = _stop_lingering_aiosqlite_connections()
    if stopped:
        print("\n[conftest] 已停止 %d 个残留的 aiosqlite 连接线程（否则进程退出时会挂死）" % stopped)


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
