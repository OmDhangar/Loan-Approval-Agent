import asyncio
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

async def node_a(state):
    print("EXECUTING node_a. Initial State:", state)
    
    # Simulate dispatching task
    print("Dispatched task...")
    
    # Wait for result
    print("Interrupting...")
    result = interrupt("waiting_for_a")
    print("Resumed! Result:", result)
    
    state["stage_result"] = result
    return state

async def node_b(state):
    print("EXECUTING node_b. State:", state)
    return state

def router(state):
    print("ROUTER evaluated. State:", state)
    res = state.get("stage_result", {})
    if res.get("passed"):
        print("ROUTER: returning advance")
        return "advance"
    print("ROUTER: returning retry")
    return "retry"

def build():
    graph = StateGraph(dict)
    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.set_entry_point("a")
    graph.add_conditional_edges("a", router, {"advance": "b", "retry": "a"})
    graph.add_edge("b", END)
    
    return graph.compile(
        checkpointer=MemorySaver(),
    )

async def test():
    g = build()
    config = {"configurable": {"thread_id": "1"}}
    print("--- START ---")
    await g.ainvoke({"state": "init"}, config)
    
    print("\n--- RESUME WITH COMMAND ---")
    await g.ainvoke(Command(resume={"passed": True}), config)

asyncio.run(test())
