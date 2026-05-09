"""
main.py – FastAPI app entry point

FIXES APPLIED:
  FIX-1  tts_service.warm_up() and tts_service.precompute_static_cache()
         are now explicitly awaited inside the lifespan startup block,
         BEFORE the first session can be created.
         The order matters:
           1. Redis connect  (cache reads/writes need this first)
           2. DB connect
           3. TTS warm_up    (opens the edge-tts HTTP connection)
           4. TTS precompute (synthesises + populates Redis + local FS)
           5. RabbitMQ / workers (if still used for heavy tasks)
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path

from api.routes import session, videosdk, agents, webhook
from core.config import settings
from core.redis_client import redis_client
from core.database import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Infrastructure ────────────────────────────────────────────────────
    logger.info("🚀 Starting Loan Wizard backend…")

    await redis_client.connect()
    logger.info("✅ Redis connected")

    await db.connect()
    logger.info("✅ PostgreSQL connected")

    # ── 2. TTS warm-up + static precompute (FIX-1) ──────────────────────────
    # Import here (after Redis is ready) to avoid circular imports at module load.
    from services.tts_service import tts_service

    warmed = await tts_service.warm_up()
    if warmed:
        # Only precompute if warm-up succeeded (i.e. edge-tts / provider is live)
        await tts_service.precompute_static_cache()
    else:
        logger.warning(
            "⚠️  TTS warm-up failed — static cache NOT precomputed. "
            "The greeting will synthesise on-demand for the first session."
        )

    # 2. Warm up LLM gateway (send a tiny prompt to force model load)
    # DISABLED: LLM requires 5.2 GiB but only 2.2 GiB available
    # The system will work without LLM for the greeting stage
    logger.info("⚠️  LLM warmup skipped (insufficient system memory - requires 5.2 GiB, have 2.2 GiB)")
    logger.info("ℹ️  System will work without LLM for greeting stage")

    logger.info("🎯 Loan Wizard is ready to accept sessions")

    # ── 3. Register ConversationAgent EventBus handlers ──────────────────────
    # ConversationAgent subscribes to STAGE_ENTERED in __init__.
    # Without this import, the agent's handlers are never registered and
    # the greeting/stage-opener TTS events never fire.
    try:
        from agents.conversation_agents import conversation_agent  # noqa: F401
        logger.info("✅ ConversationAgent EventBus handlers registered")
    except Exception as e:
        logger.error(f"ConversationAgent registration failed: {e}")
    yield

    # ── Graceful shutdown ────────────────────────────────────────────────────
    logger.info("🛑 Shutting down…")
    try:
        from core.rabbitmq_client import rabbitmq_client
        await rabbitmq_client.close()
    except Exception:
        pass
    try:
        from services.llm_gateway import llm_gateway
        await llm_gateway.close()
    except Exception:
        pass
    await redis_client.close()
    await db.close()


app = FastAPI(
    title="Loan Wizard – Agentic Onboarding API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(session.router,  prefix="/api/v1/session",  tags=["Session"])
app.include_router(videosdk.router, prefix="/api/v1/videosdk", tags=["VideoSDK"])
app.include_router(agents.router,   prefix="/api/v1/agents",   tags=["Agents"])
app.include_router(webhook.router,  prefix="/api/v1/webhook",  tags=["Webhook"])


# ── TTS audio file serving route ──────────────────────────────────────────────
# This route serves ALL TTS audio — both on-demand synthesised files and
# precomputed static cache files, because FIX-6 ensures both are placed in
# TTS_AUDIO_DIR (the same temp directory).

@app.get("/api/v1/session/tts/audio/{filename}", tags=["TTS"])
async def serve_tts_audio(filename: str):
    from services.tts_service import TTS_AUDIO_DIR
    audio_path = TTS_AUDIO_DIR / filename

    # Sanitise: prevent path traversal
    try:
        audio_path.resolve().relative_to(TTS_AUDIO_DIR.resolve())
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not audio_path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")

    media_type = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"
    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        headers={"Cache-Control": "max-age=3600"},  # browser caches repeated playback
    )


# ── Health + active sessions ──────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "loan-wizard-backend", "version": "1.0.0"}


@app.get("/api/v1/session/active", tags=["Session"])
async def active_sessions():
    return await redis_client.list_active_sessions()