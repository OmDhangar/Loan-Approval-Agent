import asyncio
import logging
from core.langgraph_engine import moderator_engine
from core.redis_client import redis_client
from core.rabbitmq_client import rabbitmq_client
from models.shared_state import SharedState, SessionMeta

logging.basicConfig(level=logging.INFO)

async def test():
    call_id = "test-call-123"
    await redis_client.connect()
    await rabbitmq_client.connect()
    
    meta = SessionMeta(call_id=call_id, session_token="abc")
    state = SharedState(session_meta=meta)
    await redis_client.set_state(state.redis_key(), state.to_json())

    await moderator_engine.start_session(call_id)
    
    print("\n--- RESUMING STAGE CORRECTLY ---")
    config = {"configurable": {"thread_id": call_id}}
    
    await moderator_engine._graph.aupdate_state(config, {"call_id": call_id, "stage_result": {"passed": True, "escalate": False}})
    
    res = await moderator_engine._graph.ainvoke(None, config=config)
    print("Result state:", res)
    
    await redis_client.close()
    await rabbitmq_client.close()

if __name__ == "__main__":
    asyncio.run(test())
