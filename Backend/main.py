"""
Poonawalla Fincorp – Loan Wizard 2026
Agentic AI Video Call–Based Onboarding System
Main FastAPI application entry point
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from api.routes import session, videosdk, agents, webhook
from core.config import settings
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from core.langgraph_engine import moderator_engine
from core.database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle"""
    logger.info("🚀 Starting Loan Wizard backend...")

    # Connect Redis
    await redis_client.connect()
    logger.info("✅ Redis connected")

    # Connect RabbitMQ
    await rabbitmq_client.connect()
    logger.info("✅ RabbitMQ connected")

    # Connect PostgreSQL
    await db.connect()
    logger.info("✅ PostgreSQL connected")

    # Start background agent workers
    await rabbitmq_client.start_workers()
    logger.info("✅ Agent workers started")

    yield

    # Graceful shutdown
    logger.info("🛑 Shutting down Loan Wizard backend...")
    await rabbitmq_client.close()
    await redis_client.close()
    await db.close()

app = FastAPI(
    title="Loan Wizard – Agentic Onboarding API",
    version="1.0.0",
    description="Agentic AI Video Call–Based Loan Onboarding | Poonawalla Fincorp",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from mock_bureau.router import router as mock_bureau_router

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(session.router,  prefix="/api/v1/session",  tags=["Session"])
app.include_router(videosdk.router, prefix="/api/v1/videosdk", tags=["VideoSDK"])
app.include_router(agents.router,   prefix="/api/v1/agents",   tags=["Agents"])
app.include_router(webhook.router,  prefix="/api/v1/webhook",  tags=["Webhook"])
app.include_router(mock_bureau_router, prefix="/api/v1/bureau", tags=["Mock Bureau"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "loan-wizard-backend"}
