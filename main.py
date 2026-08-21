"""
Entry point. Simulates multi-turn conversations to show the
verification gate and the security boundary working.

Each conversation gets its own thread_id. The graph is compiled with a
checkpointer, so state (including the verified customer_id) persists per
thread and each turn passes ONLY the new message - no hand-threading of
the state dict.
"""

from langchain_core.messages import HumanMessage
from graph import graph
from agents import TEST_MODE


def conversation(thread_id, title, turns):
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    config = {"configurable": {"thread_id": thread_id}}

    for turn in turns:
        print(f"\n  USER: {turn}")
        state = graph.invoke({"messages": [HumanMessage(content=turn)]}, config)
        last = state["messages"][-1]
        print(f"  [{getattr(last, 'name', '?')}] {last.content}")
        print(f"     (verified={state['verified']}, customer_id={state['customer_id']})")


if __name__ == "__main__":
    print(f"Running in {'TEST MODE' if TEST_MODE else 'LIVE MODE'}")

    conversation("demo-1", "1. Unverified user is blocked, then verifies, then gets data", [
        "Where is my order ORD1002?",
        "ananya.s@example.com",
        "Where is my order ORD1002?",
    ])

    conversation("demo-2", "2. SECURITY: verified customer 1 tries to read customer 2's order", [
        "1",
        "Show me order ORD1003",
    ])

    conversation("demo-3", "3. Listing all orders on the account", [
        "rohit.v@example.com",
        "what orders do I have?",
    ])

    conversation("demo-4", "4. Policy question after verifying by phone", [
        "+91-99001 12233",
        "what's the policy on a damaged item?",
    ])

    conversation("demo-5", "5. Identity AND question in a single message", [
        "Hi, I'm customer 1 - where is my order ORD1002?",
    ])
