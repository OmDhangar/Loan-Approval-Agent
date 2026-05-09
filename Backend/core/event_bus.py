"""
EventBus – In-Process Async Event Router
─────────────────────────────────────────
Replaces RabbitMQ for all real-time conversational paths.
Agents subscribe to named events and react to state changes.

RabbitMQ is still used ONLY for:
  - Heavy async background work (bureau fetch is already async via httpx)
  - Human escalation notifications
  - Audit log writes to PostgreSQL

Event contract:
  Every event payload MUST contain: call_id, event (name), ts (unix float)
  Additional keys are event-specific.

Usage:
    from core.event_bus import event_bus

    # Subscribe (at startup, in agent __init__ or module level)
    @event_bus.on("CONSENT_CAPTURED")
    async def handle_consent(data: dict): ...

    # Emit (anywhere in the call path)
    await event_bus.emit("CONSENT_CAPTURED", {"call_id": ..., "ts": time.time()})
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable, Awaitable, Any

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class EventBus:
    """
    Single-process async pub/sub.
    Handlers are called concurrently via asyncio.gather.
    Handler exceptions are logged but do NOT crash the bus.
    """

    def __init__(self):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str):
        """Decorator to register an async handler for a named event."""
        def decorator(fn: Handler) -> Handler:
            self._handlers[event].append(fn)
            logger.debug(f"EventBus: registered handler {fn.__name__!r} for '{event}'")
            return fn
        return decorator

    def subscribe(self, event: str, handler: Handler):
        """Programmatically subscribe a handler (use when decorator not convenient)."""
        self._handlers[event].append(handler)

    async def emit(self, event: str, data: dict):
        """
        Emit an event to all registered handlers.
        Runs all handlers concurrently; individual failures are isolated.
        """
        data.setdefault("event", event)
        data.setdefault("ts", time.time())

        handlers = self._handlers.get(event, [])
        if not handlers:
            logger.debug(f"EventBus: no handlers for '{event}'")
            return

        results = await asyncio.gather(
            *[self._run(h, data) for h in handlers],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"EventBus handler error for '{event}': {r}", exc_info=r)

    async def _run(self, handler: Handler, data: dict):
        try:
            await handler(data)
        except Exception as exc:
            raise exc   # Re-raise for gather to capture


# ── Singleton ─────────────────────────────────────────────────────────────────

event_bus = EventBus()


# ── Well-known event names (import these instead of raw strings) ──────────────

class Events:
    # Stage progression
    CONSENT_CAPTURED        = "CONSENT_CAPTURED"
    DOCUMENT_UPLOADED       = "DOCUMENT_UPLOADED"
    DOCUMENT_VERIFIED       = "DOCUMENT_VERIFIED"
    AADHAAR_OTP_VERIFIED    = "AADHAAR_OTP_VERIFIED"
    IDENTITY_VERIFIED       = "IDENTITY_VERIFIED"
    INCOME_CAPTURED         = "INCOME_CAPTURED"
    LOAN_PURPOSE_CAPTURED   = "LOAN_PURPOSE_CAPTURED"
    RISK_ASSESSED           = "RISK_ASSESSED"
    OFFER_READY             = "OFFER_READY"
    SESSION_COMPLETED       = "SESSION_COMPLETED"
    SESSION_ESCALATED       = "SESSION_ESCALATED"

    # STT / conversation
    UTTERANCE_PROCESSED     = "UTTERANCE_PROCESSED"
    LOW_CONFIDENCE_SPEECH   = "LOW_CONFIDENCE_SPEECH"

    # Stage entry (fired when orchestrator enters a stage)
    STAGE_ENTERED           = "STAGE_ENTERED"

    # Document
    DOCUMENT_AUTHENTICITY_CHECKED = "DOCUMENT_AUTHENTICITY_CHECKED"