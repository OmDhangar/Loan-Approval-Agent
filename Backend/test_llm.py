import asyncio
import logging
import time
from services.llm_gateway import llm_gateway
from core.config import settings

# Set up logging to see our new latency logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

async def test_llm():
    print(f"--- Testing LLM ({settings.LLM_MODEL_SMALL}) ---")
    print("This will test our new 60s timeout and persistent client...")
    
    start_time = time.time()
    
    # Test simple text generation
    response = await llm_gateway.generate_text(
        model=settings.LLM_MODEL_SMALL,
        prompt="Explain what a personal loan is in 20 words.",
        force_json=False
    )
    
    if response:
        print(f"\n[SUCCESS] Response received in {time.time() - start_time:.2f}s:")
        print(f"Result: {response}")
    else:
        print("\n[FAILED] LLM did not return a response (check if Ollama is running).")

    # Clean up the client
    await llm_gateway.close()

if __name__ == "__main__":
    try:
        asyncio.run(test_llm())
    except KeyboardInterrupt:
        pass
