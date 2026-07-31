import pytest
from ane.database.engine import init_db

@pytest.mark.asyncio
async def test_init_db_hangs():
    await init_db()
    print("init_db OK")
