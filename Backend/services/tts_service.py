"""
Text-to-Speech Service  (edge-tts + ElevenLabs + local fallback)
─────────────────────────────────────────────────────────────────
Converts AI agent text responses to audio for voice interaction during video calls.

Supported providers (in recommended order):
1. **edge-tts**    – Free, high-quality Microsoft Edge neural voices (recommended)
                     400+ voices, 50+ languages, no API key needed
2. **ElevenLabs**  – Ultra-realistic, human-like voices (paid plan required)
3. **Local pyttsx3** – Offline fallback (lower quality)

Audio files are stored temporarily and served to the frontend via the
`/api/v1/session/tts/audio/<filename>` endpoint.
"""

import logging
import asyncio
import tempfile
import os
import re
import uuid
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Directory for temporary TTS audio files
TTS_AUDIO_DIR = Path(tempfile.gettempdir()) / "loanwizard_tts"
TTS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# ── Hindi detection helpers ───────────────────────────────────────────────────
# Devanagari Unicode range: U+0900 – U+097F
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _contains_hindi(text: str) -> bool:
    """Return True if the text contains Devanagari (Hindi) characters."""
    return bool(_DEVANAGARI_RE.search(text))


class TTSService:
    """Text-to-Speech synthesis for AI agent responses."""

    ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

    def __init__(self):
        self.provider = settings.TTS_PROVIDER.lower()
        self._init_provider()

    # ── Provider Initialisation ───────────────────────────────────────────────

    def _init_provider(self):
        """Initialize the TTS provider based on config."""
        if self.provider == "edge":
            self._init_edge_tts()
        elif self.provider == "elevenlabs":
            self._init_elevenlabs()
        elif self.provider == "local":
            self._init_local_tts()
        else:
            logger.warning(
                f"Unknown TTS provider '{self.provider}', falling back to edge-tts"
            )
            self.provider = "edge"
            self._init_edge_tts()

    def _init_edge_tts(self):
        """Validate edge-tts availability."""
        try:
            import edge_tts  # noqa: F401
            self._edge_ready = True
            logger.info(
                f"Edge-TTS initialised  "
                f"voice_en={settings.EDGE_TTS_VOICE_EN}  "
                f"voice_hi={settings.EDGE_TTS_VOICE_HI}  "
                f"default_lang={settings.EDGE_TTS_DEFAULT_LANG}"
            )
        except ImportError:
            logger.error(
                "edge-tts not installed. Install with: pip install edge-tts"
            )
            self._edge_ready = False
            # Fall back to local
            self._init_local_tts()

    def _init_elevenlabs(self):
        """Validate ElevenLabs configuration."""
        if not settings.ELEVENLABS_API_KEY:
            logger.error(
                "ELEVENLABS_API_KEY is not set. "
                "Get your key from https://elevenlabs.io → Profile → API Key"
            )
            self._elevenlabs_ready = False
            logger.info("Initializing edge-tts as fallback...")
            self._init_edge_tts()
        else:
            self._elevenlabs_ready = True
            logger.info(
                f"ElevenLabs TTS initialised  "
                f"voice={settings.ELEVENLABS_VOICE_ID}  "
                f"model={settings.ELEVENLABS_MODEL_ID}"
            )

    def _init_local_tts(self):
        """Initialize local pyttsx3 TTS."""
        try:
            import pyttsx3

            self.local_engine = pyttsx3.init()
            self.local_engine.setProperty("rate", 150)  # Slower for clarity
            logger.info("Local pyttsx3 TTS initialized")
        except ImportError:
            logger.error("pyttsx3 not installed. Install with: pip install pyttsx3")
            self.local_engine = None
        except Exception as e:
            logger.error(f"Failed to initialize local TTS: {e}")
            self.local_engine = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize(
        self, text: str, call_id: str, lang: Optional[str] = None
    ) -> Optional[str]:
        """
        Synthesize speech from text asynchronously.

        Args:
            text: Agent response text to convert to speech.
            call_id: Session ID for logging and file naming.
            lang: Language override ("en" or "hi"). Auto-detected if None.

        Returns:
            Path to the generated audio file, or ``None`` if synthesis fails.
        """
        if not text or len(text.strip()) == 0:
            logger.warning("Empty text provided to TTS")
            return None

        # Truncate very long responses to keep synthesis time reasonable
        max_chars = 500
        if len(text) > max_chars:
            logger.warning(f"TTS text truncated from {len(text)} to {max_chars} chars")
            text = text[:max_chars] + "…"

        # Auto-detect language if not specified
        if lang is None:
            lang = "hi" if _contains_hindi(text) else settings.EDGE_TTS_DEFAULT_LANG

        try:
            # Try providers in priority order
            if self.provider == "edge" and getattr(self, "_edge_ready", False):
                return await self._synthesize_edge_tts(text, call_id, lang)
            elif self.provider == "elevenlabs" and getattr(
                self, "_elevenlabs_ready", False
            ):
                return await self._synthesize_elevenlabs(text, call_id)
            else:
                # Try edge-tts as universal fallback before pyttsx3
                if getattr(self, "_edge_ready", False):
                    return await self._synthesize_edge_tts(text, call_id, lang)
                return await self._synthesize_local(text, call_id)
        except Exception as e:
            logger.error(
                f"TTS synthesis failed for call {call_id}: {e}", exc_info=True
            )
            return None

    # ── Edge-TTS (FREE, recommended) ──────────────────────────────────────────

    async def _synthesize_edge_tts(
        self, text: str, call_id: str, lang: str = "en"
    ) -> Optional[str]:
        """
        Synthesize using Microsoft Edge's free neural TTS service.

        Features:
        - 400+ neural voices across 50+ languages
        - No API key required
        - High quality, natural-sounding output
        - Async-native (perfect for FastAPI)
        - Supports Hindi (hi-IN) and Indian English (en-IN)
        """
        import edge_tts

        # Select voice based on language
        if lang == "hi":
            voice = settings.EDGE_TTS_VOICE_HI
        else:
            voice = settings.EDGE_TTS_VOICE_EN

        try:
            communicate = edge_tts.Communicate(text, voice)
            filename = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.mp3"
            audio_path = TTS_AUDIO_DIR / filename
            await communicate.save(str(audio_path))

            file_size = audio_path.stat().st_size
            logger.info(
                f"Edge-TTS synthesis OK for call {call_id}  "
                f"(voice={voice}, lang={lang}, {file_size} bytes → {audio_path})"
            )
            return str(audio_path)

        except Exception as e:
            logger.error(f"Edge-TTS synthesis failed: {e}")
            # Fall back to local if edge-tts fails (e.g. no internet)
            return await self._synthesize_local(text, call_id)

    # ── ElevenLabs (paid, optional) ───────────────────────────────────────────

    async def _synthesize_elevenlabs(
        self, text: str, call_id: str
    ) -> Optional[str]:
        """
        Synthesize using ElevenLabs text-to-speech API.

        Endpoint: POST /v1/text-to-speech/{voice_id}
        Docs:     https://elevenlabs.io/docs/api-reference/text-to-speech
        """
        voice_id = settings.ELEVENLABS_VOICE_ID
        url = f"{self.ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"

        headers = {
            "xi-api-key": settings.ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

        payload = {
            "text": text,
            "model_id": settings.ELEVENLABS_MODEL_ID,
            "voice_settings": {
                "stability": settings.ELEVENLABS_STABILITY,
                "similarity_boost": settings.ELEVENLABS_SIMILARITY_BOOST,
                "style": settings.ELEVENLABS_STYLE,
                "use_speaker_boost": True,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code != 200:
                    error_body = resp.text
                    logger.error(
                        f"ElevenLabs API error {resp.status_code}: {error_body}"
                    )
                    return None

                # Save MP3 bytes to temp file
                filename = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.mp3"
                audio_path = TTS_AUDIO_DIR / filename
                audio_path.write_bytes(resp.content)

                logger.info(
                    f"ElevenLabs TTS synthesis OK for call {call_id}  "
                    f"({len(resp.content)} bytes → {audio_path})"
                )
                return str(audio_path)

        except httpx.TimeoutException:
            logger.error(f"ElevenLabs TTS timed out for call {call_id}")
            return None
        except Exception as e:
            logger.error(f"ElevenLabs TTS synthesis failed: {e}")
            return None

    # ── Local pyttsx3 (fallback) ──────────────────────────────────────────────

    async def _synthesize_local(self, text: str, call_id: str) -> Optional[str]:
        """Synthesize using local pyttsx3."""
        if not getattr(self, "local_engine", None):
            logger.error("Local TTS engine not initialized")
            return None

        try:
            loop = asyncio.get_event_loop()

            def _synthesize():
                filename = f"tts_{call_id}_{uuid.uuid4().hex[:8]}.wav"
                audio_path = str(TTS_AUDIO_DIR / filename)
                self.local_engine.save_to_file(text, audio_path)
                self.local_engine.runAndWait()
                return audio_path

            audio_path = await loop.run_in_executor(None, _synthesize)
            logger.info(f"Local TTS synthesis OK for call {call_id}: {audio_path}")
            return audio_path

        except Exception as e:
            logger.error(f"Local TTS synthesis failed: {e}")
            return None

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def cleanup_audio_file(self, audio_path: Optional[str]):
        """Delete temporary audio file after it's been served."""
        if audio_path and os.path.exists(audio_path):
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, os.unlink, audio_path)
                logger.debug(f"Cleaned up audio file: {audio_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup audio file {audio_path}: {e}")


# Global instance
tts_service = TTSService()
