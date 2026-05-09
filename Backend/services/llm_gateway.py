"""
Centralized LLM gateway for deterministic and schema-safe generation.

FIXES APPLIED:
  FIX-6  Persistent httpx.AsyncClient — reused across all requests instead of
         creating a new one per call (saves TCP handshake overhead).
  FIX-7  Default timeout increased from 30s to 60s for CPU-bound Ollama.
  FIX-8  Retry logic with exponential backoff (3 attempts: 0s, 2s, 4s).
  FIX-9  generate_structured() num_predict reduced from 120 to 40 for faster
         conversational replies.
  FIX-10 Latency logging for every LLM call.
"""
import json
import logging
import time
import asyncio
from typing import Any, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self):
        # FIX-6: Persistent client — reused for all requests
        self.client: Optional[httpx.AsyncClient] = None
        self._default_timeout = 60.0  # FIX-7: 60s default

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init persistent client (created on first use)."""
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=self._default_timeout)
            logger.info("LLM httpx client created (timeout=%.0fs)", self._default_timeout)
        return self.client

    async def close(self):
        """Graceful shutdown — call during app lifespan teardown."""
        if self.client and not self.client.is_closed:
            await self.client.aclose()
            logger.info("LLM httpx client closed")

    def _options(self, num_predict: int) -> dict[str, Any]:
        return {
            "num_predict": num_predict,
            "temperature": settings.LLM_DEFAULT_TEMPERATURE,
            "top_p": settings.LLM_DEFAULT_TOP_P,
        }

    async def warmup(self) -> bool:
        """
        Pre-load the LLM model into GPU VRAM by sending a tiny prompt.
        Call this during server startup to eliminate cold-start latency
        on the first real customer request.
        """
        logger.info("⏳ Checking and pulling LLM model if missing: %s ...", settings.LLM_MODEL_SMALL)
        try:
            client = await self._get_client()

            # 1. Pull model if it doesn't exist (this can take several minutes if downloading)
            pull_resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/pull",
                json={"name": settings.LLM_MODEL_SMALL},
                timeout=600.0,  # Pull can be very slow
            )
            if pull_resp.status_code != 200:
                logger.warning("LLM model pull returned status %s: %s", pull_resp.status_code, pull_resp.text[:200])

            # 2. Warm up the model by sending a tiny prompt (loads model into VRAM)
            logger.info("⏳ Warming up LLM model: %s (this may take a few minutes on first run)...", settings.LLM_MODEL_SMALL)
            resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": settings.LLM_MODEL_SMALL,
                    "prompt": "Say OK.",
                    "stream": False,
                    "options": {"num_predict": 3},
                    "keep_alive": "-1m",   # Keep model loaded indefinitely
                },
                timeout=600.0,
            )
            if resp.status_code == 200:
                logger.info("✅ LLM model warmed up successfully")
                return True
            logger.warning("LLM warmup returned status %s", resp.status_code)
        except Exception as exc:
            logger.warning("LLM warmup failed (non-fatal): %s", exc)
        return False

    async def generate_text(
        self,
        *,
        model: str,
        prompt: str,
        num_predict: int = 80,
        timeout: int = 60,       # FIX-7: 30 → 60
        force_json: bool = True,
        max_retries: int = 3,    # FIX-8: retry count
    ) -> Optional[str]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": self._options(num_predict),
            "keep_alive": "-1m",   # Valid Go duration: keep model loaded indefinitely
        }
        if force_json:
            payload["format"] = "json"

        # FIX-8: Retry with exponential backoff
        backoff_delays = [0, 2, 4]  # seconds between retries
        client = await self._get_client()

        for attempt in range(max_retries):
            t_start = time.time()
            try:
                resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                    timeout=float(timeout),
                )
                elapsed = time.time() - t_start

                if resp.status_code == 200:
                    result = str(resp.json().get("response", "")).strip()
                    # FIX-10: Latency logging
                    logger.info(
                        "LLM generate_text OK  model=%s  tokens=%d  elapsed=%.2fs  attempt=%d/%d",
                        model, num_predict, elapsed, attempt + 1, max_retries,
                    )
                    return result

                logger.warning(
                    "LLM text generation failed with status %s (attempt %d/%d, %.2fs): %s",
                    resp.status_code, attempt + 1, max_retries, elapsed, resp.text[:200],
                )
            except httpx.TimeoutException as exc:
                elapsed = time.time() - t_start
                logger.warning(
                    "LLM timeout (attempt %d/%d, %.2fs): %s",
                    attempt + 1, max_retries, elapsed, exc,
                )
            except Exception as exc:
                elapsed = time.time() - t_start
                logger.error(
                    "LLM error (attempt %d/%d, %.2fs): %s",
                    attempt + 1, max_retries, elapsed, exc, exc_info=True,
                )

            # Wait before retry (skip wait on last attempt)
            if attempt < max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                if delay > 0:
                    logger.info("LLM retrying in %ds...", delay)
                    await asyncio.sleep(delay)

        logger.error("LLM generate_text FAILED after %d attempts", max_retries)
        return None

    async def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        required_keys: list[str],
        num_predict: int = 40,   # FIX-9: 120 → 40 for faster responses
        timeout: int = 60,       # FIX-7: 30 → 60
    ) -> dict[str, Any]:
        raw_text = await self.generate_text(
            model=model,
            prompt=prompt,
            num_predict=num_predict,
            timeout=timeout,
        )
        if not raw_text:
            return {}
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict) and all(k in data for k in required_keys):
                return data
            if isinstance(data, dict):
                return data
        except Exception:
            logger.warning("Structured parse failed, returning empty payload")
        return {}


llm_gateway = LLMGateway()
