#!/usr/bin/env python3
"""
Database migration script.
Run once on fresh PostgreSQL to create all tables.
Usage: python infra/scripts/migrate.py
"""
import asyncio
import asyncpg
import os
import sys

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/loanwizard"
).replace("postgresql+asyncpg://", "postgresql://")


async def run():
    print(f"Connecting to: {DATABASE_URL}")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        with open("infra/init.sql") as f:
            sql = f.read()
        await conn.execute(sql)
        print("✅ Migration complete — all tables created")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())