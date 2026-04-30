"""
RabbitMQ Client – Agent task queue
Each agent type has its own queue. Moderator publishes;
agents consume and write results back to Shared State.
"""
import json
import asyncio
import logging
from contextlib import suppress
from typing import Callable, Dict

import aio_pika
from aiormq.exceptions import ChannelInvalidStateError
from aio_pika import connect_robust, Message, DeliveryMode

from core.config import settings

logger = logging.getLogger(__name__)

AGENT_QUEUES = {
    "conversation":  "q.agent.conversation",
    "verification":  "q.agent.verification",
    "vision":        "q.agent.vision",
    "risk":          "q.agent.risk",
    "offer":         "q.agent.offer",
    "compliance":    "q.agent.compliance",
    "human_oversight": "q.human.oversight",
    "stt_pipeline":  "q.stt.pipeline",
}


class RabbitMQClient:
    def __init__(self):
        self._connection = None
        self._channel = None
        self._handlers: Dict[str, Callable] = {}
        self._worker_tasks: list[asyncio.Task] = []

    async def connect(self):
        self._connection = await connect_robust(settings.RABBITMQ_URL)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=10)

        # Declare all queues
        for queue_name in AGENT_QUEUES.values():
            await self._channel.declare_queue(
                queue_name,
                durable=True,
                arguments={"x-dead-letter-exchange": "dlx.agents"},
            )
        logger.info("RabbitMQ queues declared")

    async def close(self):
        await self.stop_workers()
        if self._connection:
            with suppress(Exception):
                await self._connection.close()
            self._connection = None
            self._channel = None

    # ── Publisher ─────────────────────────────────────────────────────────────

    async def publish_task(self, agent_type: str, payload: dict) -> None:
        """Publish a task for a specific agent type."""
        queue_name = AGENT_QUEUES.get(agent_type)
        if not queue_name:
            raise ValueError(f"Unknown agent type: {agent_type}")

        if await self._should_skip_payload(agent_type, payload):
            logger.debug(
                "Skipping task publish for stopped/terminal session: agent=%s call_id=%s",
                agent_type,
                payload.get("call_id"),
            )
            return

        message = Message(
            body=json.dumps(payload, default=str).encode(),
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self._channel.default_exchange.publish(
            message,
            routing_key=queue_name,
        )
        logger.debug(f"Task published to {agent_type}: call_id={payload.get('call_id')}")

    # ── Consumer registration ─────────────────────────────────────────────────

    def register_handler(self, agent_type: str, handler: Callable):
        self._handlers[agent_type] = handler

    async def start_workers(self):
        """Start consuming from all registered queues."""
        if any(not task.done() for task in self._worker_tasks):
            logger.info("RabbitMQ workers already running")
            return

        from agents.conversation_agents import ConversationAgent
        from agents.verification_agent import VerificationAgent
        from agents.vision_agent import VisionAgent
        from agents.risk_agent import RiskAgent
        from agents.offer_agent import OfferAgent
        from agents.compliance_agent import ComplianceAgent
        from agents.stt_pipeline import STTPipeline

        agent_map = {
            "conversation": ConversationAgent(),
            "verification": VerificationAgent(),
            "vision":       VisionAgent(),
            "risk":         RiskAgent(),
            "offer":        OfferAgent(),
            "compliance":   ComplianceAgent(),
            "stt_pipeline": STTPipeline(),
        }

        self._worker_tasks = []
        for agent_type, agent in agent_map.items():
            queue_name = AGENT_QUEUES[agent_type]
            queue = await self._channel.get_queue(queue_name)
            task = asyncio.create_task(
                self._consume(agent_type, queue, agent.handle_task),
                name=f"rabbitmq-worker-{agent_type}",
            )
            self._worker_tasks.append(task)
            logger.info(f"Worker started for {agent_type}")

    async def stop_workers(self):
        """Cancel active queue consumers before closing RabbitMQ resources."""
        if not self._worker_tasks:
            return

        logger.info("Stopping RabbitMQ workers...")
        for task in self._worker_tasks:
            task.cancel()

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*self._worker_tasks, return_exceptions=True),
                timeout=5,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for RabbitMQ workers to stop")
            results = []
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, ChannelInvalidStateError):
                continue
            if isinstance(result, Exception):
                logger.debug("Worker stopped with exception during shutdown", exc_info=result)

        self._worker_tasks = []
        logger.info("RabbitMQ workers stopped")

    async def _consume(self, agent_type: str, queue, handler: Callable):
        try:
            async with queue.iterator() as q_iter:
                async for message in q_iter:
                    try:
                        async with message.process(requeue=True):
                            payload = json.loads(message.body.decode())
                            if await self._should_skip_payload(agent_type, payload):
                                logger.debug(
                                    "Discarding queued task for stopped/terminal session: agent=%s call_id=%s",
                                    agent_type,
                                    payload.get("call_id"),
                                )
                                continue
                            await handler(payload)
                    except asyncio.CancelledError:
                        logger.debug("Worker cancelled for %s", agent_type)
                        raise
                    except ChannelInvalidStateError:
                        logger.debug("RabbitMQ channel closed while stopping %s worker", agent_type)
                        break
                    except Exception as e:
                        logger.error(f"Agent handler error: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.debug("Worker task cancelled for %s", agent_type)
            raise
        except ChannelInvalidStateError:
            logger.debug("RabbitMQ channel closed for %s worker", agent_type)

    async def _should_skip_payload(self, agent_type: str, payload: dict) -> bool:
        """Return True when a queued task belongs to a session that should not run."""
        call_id = payload.get("call_id")
        if not call_id:
            return False

        call_id = str(call_id)
        try:
            from core.redis_client import redis_client
            from models.shared_state import SharedState, SessionStage

            if await redis_client.get_state(f"session:{call_id}:stopped"):
                return True

            raw = await redis_client.get_state(f"session:{call_id}:state")
            if not raw:
                return True

            state = SharedState.from_json(raw)
            if state.current_stage in (SessionStage.COMPLETED, SessionStage.ABANDONED):
                return True
            if state.current_stage == SessionStage.ESCALATED and agent_type != "human_oversight":
                return True
        except Exception:
            logger.debug("Unable to check session task guard for call_id=%s", call_id, exc_info=True)
            return False

        return False


rabbitmq_client = RabbitMQClient()
