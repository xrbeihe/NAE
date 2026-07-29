"""Migration script: add user_id column, create admin user, assign existing sessions."""
import sys
sys.path.insert(0, 'D:/ANE/backend')

import asyncio
from sqlalchemy import text, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = 'sqlite+aiosqlite:///D:/ANE/data/ane.db'

async def migrate():
    engine = create_async_engine(DATABASE_URL)

    # Step 1: Schema changes
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(sessions)"))
        cols = {row[1] for row in result.fetchall()}
        print(f"sessions columns: {cols}")

        if 'user_id' not in cols:
            await conn.execute(text("ALTER TABLE sessions ADD COLUMN user_id VARCHAR"))
            print("Added user_id column")

        from ane.database.models import Base
        await conn.run_sync(Base.metadata.create_all)
        print("Tables verified")

    # Step 2: Data migration (use raw connection to avoid async session issues)
    async with engine.connect() as conn:
        from ane.auth import hash_password

        # Create admin user via raw SQL
        result = await conn.execute(text("SELECT id FROM users WHERE username = 'admin'"))
        row = result.fetchone()
        if not row:
            import uuid
            admin_id = uuid.uuid4().hex[:12]
            pw_hash = hash_password('admin123')
            await conn.execute(
                text("INSERT INTO users (id, username, password_hash, display_name, is_active) VALUES (:id, :un, :pw, :dn, 1)"),
                {"id": admin_id, "un": "admin", "pw": pw_hash, "dn": "管理员"}
            )
            print(f"Created admin user: {admin_id}")
        else:
            admin_id = row[0]
            print(f"Admin exists: {admin_id}")

        # Assign orphan sessions to admin
        result = await conn.execute(text("SELECT COUNT(*) FROM sessions WHERE user_id IS NULL"))
        null_count = result.scalar()
        if null_count:
            await conn.execute(
                text(f"UPDATE sessions SET user_id = '{admin_id}' WHERE user_id IS NULL")
            )
            print(f"Assigned {null_count} sessions to admin")
        else:
            print("No orphan sessions to assign")

        await conn.commit()

        # Verify
        result = await conn.execute(text("SELECT COUNT(*) FROM sessions"))
        total = result.scalar()
        result = await conn.execute(text(f"SELECT COUNT(*) FROM sessions WHERE user_id = '{admin_id}'"))
        admin_total = result.scalar()
        print(f"Total sessions: {total}, admin has: {admin_total}")

    await engine.dispose()
    print("Migration complete")

asyncio.run(migrate())
