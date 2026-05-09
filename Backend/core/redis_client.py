"""
Redis Client – SharedState persistence layer

FIXES:
  FIX-BUFFER  Two new methods: append_event_buffer() / get_and_clear_event_buffer()
              Used by the SSE endpoint to guarantee no events are lost before
              the frontend's EventSource connection is established.
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
        logger.info("Redis connected")

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

                    state_obj       = json.loads(raw)
                    current_version = int(state_obj.get("version", 0))
                    if current_version != expected_version:
                        await pipe.reset()
                        return False, current_version

                    merged            = self._deep_merge_dict(state_obj, patch)
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

    # ── TTS Cache ─────────────────────────────────────────────────────────────

    async def set_tts_cache(
        self, cache_key: str, audio_path: str, ttl: int | None = None
    ) -> None:
        try:
            if ttl is None:
                await self._client.set(cache_key, audio_path)
            else:
                await self._client.set(cache_key, audio_path, ex=ttl)
            logger.debug(f"TTS cache set: {cache_key}")
        except Exception as e:
            logger.warning(f"set_tts_cache failed [{cache_key}]: {e}")

    async def get_tts_cache(self, cache_key: str) -> Optional[str]:
        try:
            value = await self._client.get(cache_key)
            if value:
                logger.debug(f"TTS cache hit: {cache_key}")
            return value
        except Exception as e:
            logger.warning(f"get_tts_cache failed [{cache_key}]: {e}")
            return None

    # ── SSE Event Replay Buffer ────────────────────────────────────────────────
    # FIX-BUFFER: Events published while no SSE subscriber is listening are
    # pushed into a short-lived Redis list. When the SSE connection is
    # established (which always happens 200-800ms after /join returns) the
    # generator replays the buffer first, then switches to live pub/sub.
    # This completely eliminates the race condition where the greeting is
    # published before the frontend EventSource is connected.

    _BUFFER_KEY_PREFIX = "session_evt_buf"
    _BUFFER_TTL        = 30          # seconds — events older than this are irrelevant
    _BUFFER_MAX_LEN    = 50          # guard against unbounded growth

    def _buf_key(self, call_id: str) -> str:
        return f"{self._BUFFER_KEY_PREFIX}:{call_id}"

    async def append_event_buffer(self, call_id: str, message: dict) -> None:
        """
        Push an event to the replay buffer AND to the pub/sub channel.
        Called by publish() — callers do not need to change.
        """
        key = self._buf_key(call_id)
        try:
            serialised = json.dumps(message)
            pipe = self._client.pipeline()
            pipe.rpush(key, serialised)
            pipe.ltrim(key, -self._BUFFER_MAX_LEN, -1)   # keep last N
            pipe.expire(key, self._BUFFER_TTL)
            await pipe.execute()
        except Exception as e:
            logger.debug(f"Event buffer append failed (non-fatal): {e}")

    async def get_and_clear_event_buffer(self, call_id: str) -> list[str]:
        """
        Atomically return all buffered events and delete the buffer.
        Called once when the SSE generator starts.
        Returns a list of raw JSON strings, oldest-first.
        """
        key = self._buf_key(call_id)
        try:
            pipe = self._client.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            results = await pipe.execute()
            return results[0] if results else []
        except Exception as e:
            logger.debug(f"Event buffer read failed (non-fatal): {e}")
            return []

    # ── Pub/Sub ───────────────────────────────────────────────────────────────

    async def publish(self, channel: str, message: dict) -> None:
        """
        Publish to Redis pub/sub AND append to the replay buffer when the
        channel is a session events channel.
        """
        serialised = json.dumps(message)
        await self._client.publish(channel, serialised)

        # Buffer only session-level event channels
        if channel.startswith("session:") and channel.endswith(":events"):
            call_id = channel.split(":")[1]
            await self.append_event_buffer(call_id, message)

    async def subscribe(self, channel: str):
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    # ── Session registry ──────────────────────────────────────────────────────

    async def register_session(self, call_id: str, meta: dict) -> None:
        try:
            await self._client.hset("active_sessions", call_id, json.dumps(meta))
            await self._client.set(
                f"session_meta:{call_id}",
                json.dumps(meta),
                ex=settings.REDIS_STATE_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning(f"register_session failed [{call_id}]: {e}")

    async def unregister_session(self, call_id: str) -> None:
        await self._client.hdel("active_sessions", call_id)
        await self._client.delete(f"session_meta:{call_id}")

    async def list_active_sessions(self) -> list:
        raw = await self._client.hgetall("active_sessions")
        return [json.loads(v) for v in raw.values()]

    # ── Network quality cache ─────────────────────────────────────────────────

    async def cache_quality_score(self, call_id: str, score: int) -> None:
        await self._client.set(f"quality:{call_id}", score, ex=10)

    async def get_quality_score(self, call_id: str) -> int:
        val = await self._client.get(f"quality:{call_id}")
        return int(val) if val else 5

    # ── Key scanning ──────────────────────────────────────────────────────────

    async def scan_keys(self, pattern: str) -> list[str]:
        keys = []
        async for key in self._client.scan_iter(match=pattern, count=100):
            keys.append(key)
            if len(keys) >= 10:
                break
        return keys


redis_client = RedisClient()