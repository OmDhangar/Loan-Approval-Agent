"""
Core settings – loaded from environment variables (.env)
"""
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "https://loanwizard.poonawallafincorp.com"]

    # ── VideoSDK (replaces raw Mediasoup/WebRTC plumbing) ────────────────────
    VIDEOSDK_API_KEY: str = ""          # From app.videosdk.live dashboard
    VIDEOSDK_SECRET_KEY: str = ""       # Used to sign JWT tokens
    VIDEOSDK_API_ENDPOINT: str = "https://api.videosdk.live/v2"
    VIDEOSDK_TOKEN_EXPIRY_MINUTES: int = 60

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_STATE_TTL_SECONDS: int = 7200   # 2 hours per session

    # ── RabbitMQ ──────────────────────────────────────────────────────────────
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/loanwizard"

    # ── AWS S3 (Mumbai) ───────────────────────────────────────────────────────
    AWS_REGION: str = "ap-south-1"
    S3_BUCKET_RECORDINGS: str = "loanwizard-recordings-mumbai"
    S3_BUCKET_AUDIT: str = "loanwizard-audit-mumbai"

    # ── LLM (local) ───────────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL_LARGE: str = "llama3.1:8b"   # Offer Agent (use llama3.1:8b until gemma3:27b is pulled)
    LLM_MODEL_SMALL: str = "llama3.1:8b"   # Conversation Agent
    LLM_DEFAULT_TEMPERATURE: float = 0.0
    LLM_DEFAULT_TOP_P: float = 0.1
    LLM_GATEWAY_ONLY: bool = True

    # ── Text-to-Speech (TTS) ──────────────────────────────────────────────────
    TTS_PROVIDER: str = "edge"              # edge | elevenlabs | local (pyttsx3)
    # Edge-TTS (free, no API key, multilingual neural voices from Microsoft)
    EDGE_TTS_VOICE_EN: str = "en-IN-NeerjaNeural"      # Indian English female
    EDGE_TTS_VOICE_HI: str = "hi-IN-SwaraNeural"       # Hindi female
    EDGE_TTS_DEFAULT_LANG: str = "en"                   # en | hi
    # ElevenLabs (optional premium – set key in .env)
    # ELEVENLABS_API_KEY: str = "sk_85e487c7aaf3cca97e8336c4eb9398b2ac4644ca415aaa21"            # From https://elevenlabs.io → Profile → API Key
    # ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel (warm, natural)
    # ELEVENLABS_MODEL_ID: str = "eleven_multilingual_v2" # Best quality multilingual
    # ELEVENLABS_STABILITY: float = 0.5       # 0-1: lower = more expressive
    # ELEVENLABS_SIMILARITY_BOOST: float = 0.75  # 0-1: higher = closer to original voice
    # ELEVENLABS_STYLE: float = 0.0           # 0-1: style exaggeration

    # ── Whisper STT ───────────────────────────────────────────────────────────
    WHISPER_MODEL: str = "large-v3"
    WHISPER_CONFIDENCE_THRESHOLD: float = 0.75

    # ── Vision ────────────────────────────────────────────────────────────────
    YOLO_MODEL_PATH: str = "yolov8n.pt"
    VISION_CONFIDENCE_THRESHOLD: float = 0.50

    # ── Credit Bureau (mock for MVP) ──────────────────────────────────────────
    BUREAU_API_URL: str = "http://localhost:8000/api/v1/bureau"
    BUREAU_API_KEY: str = "mock-bureau-key-123"

    # ── Human Oversight ───────────────────────────────────────────────────────
    HUMAN_ESCALATION_QUEUE: str = "human_oversight"
    STRICT_SUPERVISOR: bool = True
    ZERO_TRUST_INCOME: bool = True
    GEO_HARD_GATE: bool = True

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
