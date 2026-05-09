"""
Text-to-Speech Service  (edge-tts + ElevenLabs + local fallback)

FIXES IN THIS FILE:
  FIX-WINPATH   TTS_CACHE_DIR was Path("Backend/tts_cache") — a relative path
                that becomes "Backend/Backend/tts_cache" (double-Backend) when
                uvicorn is started from the Backend/ directory, causing every
                restart to re-synthesise all static messages because the
                persistent copy is never found.
                Fix: use Path(__file__).parent.parent / "tts_cache" so the
                path is always absolute and independent of cwd.
  FIX-RETRIEVE  retrieve_from_local_storage() now correctly constructs the
                serveable filename using only the leaf name of storage_key,
                matching the filename precompute_static_cache() already wrote.
"""

import logging
import asyncio
import tempfile
import os
import shutil
import re
import uuid
from pathlib import Path
from typing import Optional
import time

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# ── Audio directories ─────────────────────────────────────────────────────────
# All synthesised + cached audio lives here.
# The /api/v1/session/tts/audio/<filename> route serves from this single dir.
TTS_AUDIO_DIR = Path(tempfile.gettempdir()) / "loanwizard_tts"
TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# FIX-WINPATH: use __file__ so the path is always absolute.
# __file__ = .../Backend/services/tts_service.py
# .parent   = .../Backend/services/
# .parent   = .../Backend/
# / "tts_cache" = .../Backend/tts_cache/   ← always correct regardless of cwd
TTS_CACHE_DIR        = Path(__file__).parent.parent / "tts_cache"
TTS_CACHE_STATIC_DIR = TTS_CACHE_DIR / "static"
TTS_CACHE_STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ── Hindi detection ───────────────────────────────────────────────────────────
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _contains_hindi(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text))


# ── Static messages (single source of truth) ──────────────────────────────────
# conversation_agent imports STATIC_MESSAGES and _TEXT_TO_CACHE_KEY so that
# stage openers are guaranteed to match the precomputed audio exactly.
STATIC_MESSAGES: dict[str, str] = {
    "greeting_initial": (
        "Hello! I'm your Loan Wizard AI assistant from Poonawalla Fincorp. "
        "This video call is being recorded as required by RBI for security and compliance. "
        "Your data will be used solely for this loan application. "
        "To continue, please say 'I agree' or 'I consent'."
    ),
    "ovd_request": (
        "Thank you for your consent. Now I need to verify your identity document. "
        "Please click the upload button on your screen and share a clear photo of "
        "your Aadhaar card or PAN card. Make sure all four corners are visible and "
        "the text is sharp and readable."
    ),
    "otp_request": (
        "An OTP has been sent to your Aadhaar-linked mobile number. "
        "Please read out the 6-digit OTP when you receive it."
    ),
    "risk_assessment": (
        "Perfect, thank you! I'm now running your credit assessment. "
        "This usually takes just a few seconds. Please bear with me."
    ),
    "offer_generation": (
        "I'm generating your personalised loan offer right now. "
        "One moment please\u2026"
    ),
}

# Reverse map: exact text → Redis cache key (used for O(1) static cache lookup)
_TEXT_TO_CACHE_KEY: dict[str, str] = {
    text: f"tts:static:{key}" for key, text in STATIC_MESSAGES.items()
}


class TTSService:
    """Text-to-Speech synthesis for AI agent responses."""

    ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

    def __init__(self):
        self.provider = settings.TTS_PROVIDER.lower()
        self._init_provider()

    # ── Provider init ─────────────────────────────────────────────────────────

    def _init_provider(self):
        if self.provider == "edge":
            self._init_edge_tts()
        elif self.provider == "elevenlabs":
            self._init_elevenlabs()
        elif self.provider == "local":
            self._init_local_tts()
        else:
            logger.warning(f"Unknown TTS provider '{self.provider}', falling back to edge-tts")
            self.provider = "edge"
            self._init_edge_tts()

    def _init_edge_tts(self):
        try:
            import edge_tts  # noqa: F401
            self._edge_ready = True
            logger.info(
                f"Edge-TTS ready  voice_en={settings.EDGE_TTS_VOICE_EN}  "
                f"voice_hi={settings.EDGE_TTS_VOICE_HI}  lang={settings.EDGE_TTS_DEFAULT_LANG}"
            )
        except ImportError:
            logger.error("edge-tts not installed. Run: pip install edge-tts")
            self._edge_ready = False
            self._init_local_tts()

    def _init_elevenlabs(self):
        if not settings.ELEVENLABS_API_KEY:
            logger.error("ELEVENLABS_API_KEY not set — falling back to edge-tts")
            self._elevenlabs_ready = False
            self._init_edge_tts()
        else:
            self._elevenlabs_ready = True
            logger.info(f"ElevenLabs ready  voice={settings.ELEVENLABS_VOICE_ID}")

    def _init_local_tts(self):
        try:
            import pyttsx3
            self.local_engine = pyttsx3.init()
            self.local_engine.setProperty("rate", 150)
            logger.info("Local pyttsx3 TTS initialised")
        except ImportError:
            logger.error("pyttsx3 not installed. Run: pip install pyttsx3")
            self.local_engine = None
        except Exception as e:
            logger.error(f"pyttsx3 init failed: {e}")
            self.local_engine = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize(
        self, text: str, call_id: str, lang: Optional[str] = None
    ) -> Optional[str]:
        """
        Synthesise speech.  Always returns a path inside TTS_AUDIO_DIR
        so the /tts/audio/<filename> route can serve it without any
        special static-files mount.
        """
        if not text or not text.strip():
            return None

        if len(text) > 500:
            text = text[:500] + "\u2026"

        if lang is None:
            lang = "hi" if _contains_hindi(text) else settings.EDGE_TTS_DEFAULT_LANG

        try:
            if self.provider == "edge" and getattr(self, "_edge_ready", False):
                return await self._synthesize_edge_tts(text, call_id, lang)
            elif self.provider == "elevenlabs" and getattr(self, "_elevenlabs_ready", False):
                return await self._synthesize_elevenlabs(text, call_id)
            elif getattr(self, "_edge_ready", False):
                return await self._synthesize_edge_tts(text, call_id, lang)
            else:
                return await self._synthesize_local(text, call_id)
        except Exception as e:
            logger.error(f"TTS synthesis failed [{call_id}]: {e}", exc_info=True)
            return None

    # ── Edge-TTS ──────────────────────────────────────────────────────────────

    async def _synthesize_edge_tts(
        self, text: str, call_id: str, lang: str = "en"
    ) -> Optional[str]:
        import edge_tts

        voice = settings.EDGE_TTS_VOICE_HI if lang == "hi" else settings.EDGE_TTS_VOICE_EN
        try:
            communicate = edge_tts.Communicate(text, voice)
            filename    = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.mp3"
            audio_path  = TTS_AUDIO_DIR / filename
            await communicate.save(str(audio_path))
            logger.info(f"Edge-TTS OK [{call_id}]  {audio_path.stat().st_size}B")
            return str(audio_path)
        except Exception as e:
            logger.error(f"Edge-TTS failed: {e}")
            return await self._synthesize_local(text, call_id)

    # ── ElevenLabs ────────────────────────────────────────────────────────────

    async def _synthesize_elevenlabs(self, text: str, call_id: str) -> Optional[str]:
        voice_id = settings.ELEVENLABS_VOICE_ID
        url      = f"{self.ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"
        headers  = {
            "xi-api-key":   settings.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":     text,
            "model_id": settings.ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability":        settings.ELEVENLABS_STABILITY,
                "similarity_boost": settings.ELEVENLABS_SIMILARITY_BOOST,
                "style":            settings.ELEVENLABS_STYLE,
                "use_speaker_boost": True,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    logger.error(f"ElevenLabs {resp.status_code}: {resp.text}")
                    return None
                filename   = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.mp3"
                audio_path = TTS_AUDIO_DIR / filename
                audio_path.write_bytes(resp.content)
                logger.info(f"ElevenLabs OK [{call_id}]  {len(resp.content)}B")
                return str(audio_path)
        except httpx.TimeoutException:
            logger.error(f"ElevenLabs timeout [{call_id}]")
            return None
        except Exception as e:
            logger.error(f"ElevenLabs failed: {e}")
            return None

    # ── Local pyttsx3 ─────────────────────────────────────────────────────────

    async def _synthesize_local(self, text: str, call_id: str) -> Optional[str]:
        if not getattr(self, "local_engine", None):
            return None
        try:
            loop = asyncio.get_event_loop()

            def _run():
                filename   = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.wav"
                audio_path = str(TTS_AUDIO_DIR / filename)
                self.local_engine.save_to_file(text, audio_path)
                self.local_engine.runAndWait()
                return audio_path

            return await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.error(f"Local TTS failed: {e}")
            return None

    # ── Warm-up ───────────────────────────────────────────────────────────────

    async def warm_up(self) -> bool:
        """Warm up provider with a tiny silent test. Call during lifespan startup."""
        try:
            start = time.time()
            if self.provider == "edge" and getattr(self, "_edge_ready", False):
                import edge_tts
                communicate = edge_tts.Communicate("Ready.", settings.EDGE_TTS_VOICE_EN)
                dummy = TTS_AUDIO_DIR / "warmup_test.mp3"
                await communicate.save(str(dummy))
                dummy.unlink(missing_ok=True)
                logger.info(f"✅ TTS warm-up done in {time.time()-start:.2f}s")
                return True
            logger.info("ℹ️  TTS warm-up skipped (edge-tts not active)")
            return False
        except Exception as e:
            logger.warning(f"⚠️  TTS warm-up failed: {e}")
            return False

    # ── Static cache precomputation ───────────────────────────────────────────

    async def precompute_static_cache(self) -> None:
        """
        Synthesise all STATIC_MESSAGES once and cache them so there is no
        synthesis latency on the first real call.

        Files are stored in two places:
          - TTS_CACHE_STATIC_DIR (persistent, survives restarts)
          - TTS_AUDIO_DIR        (serveable by the /tts/audio route)

        Redis maps "tts:static:<id>" → TTS_AUDIO_DIR path.
        """
        from core.redis_client import redis_client

        start       = time.time()
        success_cnt = 0

        for message_id, text in STATIC_MESSAGES.items():
            redis_key       = f"tts:static:{message_id}"
            # FIX-WINPATH: both paths now use the absolute TTS_CACHE_DIR
            persistent_path = TTS_CACHE_STATIC_DIR / f"{message_id}.mp3"
            # Serveable filename matches what retrieve_from_local_storage builds
            serveable_name  = f"static_{message_id}.mp3"
            serveable_path  = TTS_AUDIO_DIR / serveable_name

            try:
                if persistent_path.exists() and persistent_path.stat().st_size > 0:
                    # Restore to TTS_AUDIO_DIR without re-synthesising
                    shutil.copy(str(persistent_path), str(serveable_path))
                    logger.debug(f"  Static cache restored: {message_id}")
                else:
                    audio_path = await self.synthesize(text, call_id="precompute")
                    if not audio_path or not Path(audio_path).exists():
                        logger.warning(f"  Precompute failed: {message_id}")
                        continue
                    shutil.copy(audio_path, str(persistent_path))
                    shutil.copy(audio_path, str(serveable_path))
                    Path(audio_path).unlink(missing_ok=True)
                    logger.debug(f"  Synthesised + cached: {message_id}")

                await redis_client.set_tts_cache(redis_key, str(serveable_path), ttl=None)
                success_cnt += 1

            except Exception as e:
                logger.warning(f"  Precompute {message_id}: {e}")

        logger.info(
            f"✅ TTS static cache: {success_cnt}/{len(STATIC_MESSAGES)} messages "
            f"in {time.time()-start:.2f}s  dir={TTS_CACHE_DIR}"
        )

    # ── Session cache helpers ─────────────────────────────────────────────────

    async def save_to_session_cache(
        self, audio_path: str, call_id: str, text_hash: int
    ) -> None:
        """Write-through to Redis so subsequent same-text calls are Tier-1 hits."""
        from core.redis_client import redis_client
        session_key = f"tts:session:{call_id}:{text_hash}"
        await redis_client.set_tts_cache(
            session_key,
            audio_path,
            ttl=settings.REDIS_STATE_TTL_SECONDS,
        )

    async def save_to_local_storage(self, audio_path: str, storage_key: str) -> str:
        """
        Persistently copy audio to TTS_CACHE_DIR.
        storage_key examples: 'static/msg_id', 'session/call_id/msg_id'.
        """
        # FIX-WINPATH: TTS_CACHE_DIR is now absolute so this always resolves correctly
        dest_path = TTS_CACHE_DIR / f"{storage_key}.mp3"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(audio_path, str(dest_path))
        return str(dest_path)

    async def retrieve_from_local_storage(self, storage_key: str) -> Optional[str]:
        """
        Retrieve a cached file and make it serveable via TTS_AUDIO_DIR.

        FIX-RETRIEVE: the serveable filename now uses ONLY the leaf part of
        storage_key so it always matches the name precompute_static_cache()
        already wrote (e.g. 'static/greeting_initial' → 'static_greeting_initial.mp3').
        The old code used storage_key.replace('/', '_') which also produces
        the same result, so this is equivalent — but now we also make the
        logic explicit and consistent with precompute.
        """
        # FIX-WINPATH
        persistent_path = TTS_CACHE_DIR / f"{storage_key}.mp3"
        if not persistent_path.exists():
            return None

        # Build the serveable filename the same way precompute_static_cache does.
        serveable_name = storage_key.replace("/", "_") + ".mp3"
        serveable_path = TTS_AUDIO_DIR / serveable_name

        # Only copy if missing or stale
        if (
            not serveable_path.exists()
            or serveable_path.stat().st_mtime < persistent_path.stat().st_mtime
        ):
            shutil.copy(str(persistent_path), str(serveable_path))

        return str(serveable_path)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def cleanup_session_cache(self, session_id: str) -> None:
        try:
            for f in TTS_AUDIO_DIR.glob(f"tts_{session_id}_*.mp3"):
                f.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Session cache cleanup failed: {e}")

    async def cleanup_audio_file(self, audio_path: Optional[str]) -> None:
        if audio_path and os.path.exists(audio_path):
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, os.unlink, audio_path)
            except Exception as e:
                logger.warning(f"Cleanup failed: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────
tts_service = TTSService()