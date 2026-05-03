"""
Centralized LLM gateway for deterministic and schema-safe generation.
"""
import json
import logging
from typing import Any, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class LLMGateway:
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
            async with httpx.AsyncClient(timeout=600.0) as client:
                # 1. Pull model if it doesn't exist (this can take several minutes if downloading)
                pull_resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/pull",
                    json={"name": settings.LLM_MODEL_SMALL},
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
        timeout: int = 12,
        force_json: bool = True,
    ) -> Optional[str]:
        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": self._options(num_predict),
                "keep_alive": "-1m",   # Valid Go duration: keep model loaded indefinitely
            }
            if force_json:
                payload["format"] = "json"

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                )
                if resp.status_code == 200:
                    return str(resp.json().get("response", "")).strip()
                logger.warning("LLM text generation failed with status %s: %s",
                               resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("LLM text generation error: %s", exc)
        return None

    async def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        required_keys: list[str],
        num_predict: int = 120,
        timeout: int = 12,
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
