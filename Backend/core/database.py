"""
Database – async PostgreSQL connection pool via asyncpg.
Provides a lightweight wrapper used across route handlers and agents.
"""

import asyncpg
import logging
from core.config import settings

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        logger.info("PostgreSQL pool created")

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def execute(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetchrow(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchval(self, query: str, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)


db = Database()