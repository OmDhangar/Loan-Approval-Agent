"""
Redis Client – SharedState persistence layer
"""
import json
import logging
from typing import Optional, Any

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self._client: Optional[aioredis.Redis] = None

    async def connect(self):
        self._client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._client.ping()

    async def close(self):
        if self._client:
            await self._client.aclose()

    # ── SharedState CRUD ──────────────────────────────────────────────────────

    async def set_state(self, key: str, state_json: str, ttl: int | None = None) -> None:
        await self._client.set(
            key,
            state_json,
            ex=ttl or settings.REDIS_STATE_TTL_SECONDS,
        )

    async def compare_and_set_state(
        self,
        key: str,
        expected_version: int,
        patch: dict[str, Any],
        ttl: int | None = None,
    ) -> tuple[bool, int]:
        """
        Atomically patch state iff current version == expected_version.
        Returns (updated, resulting_version).
        """
        ttl_seconds = ttl or settings.REDIS_STATE_TTL_SECONDS
        while True:
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        await pipe.reset()
                        return False, -1

                    state_obj = json.loads(raw)
                    current_version = int(state_obj.get("version", 0))
                    if current_version != expected_version:
                        await pipe.reset()
                        return False, current_version

                    merged = self._deep_merge_dict(state_obj, patch)
                    merged["version"] = current_version + 1

                    pipe.multi()
                    pipe.set(key, json.dumps(merged), ex=ttl_seconds)
                    await pipe.execute()
                    return True, merged["version"]
                except aioredis.WatchError:
                    continue

    @staticmethod
    def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = RedisClient._deep_merge_dict(out[key], value)
            else:
                out[key] = value
        return out

    async def get_state(self, key: str) -> Optional[str]:
        return await self._client.get(key)

    async def delete_state(self, key: str) -> None:
        await self._client.delete(key)

    async def set_once(self, key: str, value: str = "1", ttl_seconds: int | None = None) -> bool:
        return bool(await self._client.set(
            key,
            value,
            ex=ttl_seconds or settings.REDIS_STATE_TTL_SECONDS,
            nx=True,
        ))

    # ── Pub/Sub for frontend real-time updates ────────────────────────────────

    async def publish(self, channel: str, message: dict) -> None:
        await self._client.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    # ── Session registry ──────────────────────────────────────────────────────

    async def register_session(self, call_id: str, meta: dict) -> None:
        await self._client.hset("active_sessions", call_id, json.dumps(meta))

    async def unregister_session(self, call_id: str) -> None:
        await self._client.hdel("active_sessions", call_id)

    async def list_active_sessions(self) -> list:
        raw = await self._client.hgetall("active_sessions")
        return [json.loads(v) for v in raw.values()]

    # ── Network quality cache ─────────────────────────────────────────────────

    async def cache_quality_score(self, call_id: str, score: int) -> None:
        await self._client.set(f"quality:{call_id}", score, ex=10)

    async def get_quality_score(self, call_id: str) -> int:
        val = await self._client.get(f"quality:{call_id}")
        return int(val) if val else 5

    # ── Key scanning (for vision agent snapshot lookup) ────────────────────────

    async def scan_keys(self, pattern: str) -> list[str]:
        """Scan Redis keys matching a glob pattern."""
        keys = []
        async for key in self._client.scan_iter(match=pattern, count=100):
            keys.append(key)
            if len(keys) >= 10:  # limit to prevent excessive scanning
                break
        return keys


redis_client = RedisClient()
