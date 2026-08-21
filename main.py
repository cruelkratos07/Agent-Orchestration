"""
Entry point. Simulates multi-turn conversations to show the
verification gate and the security boundary working.

Each conversation gets its own thread_id. The graph is compiled with a
checkpointer, so state (including the verified customer_id) persists per
thread and each turn passes ONLY the new message - no hand-threading of
the state dict.
"""

from langchain_core.messages import HumanMessage
import db
from graph import graph
from agents import TEST_MODE


def conversation(thread_id, title, turns):
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    config = {"configurable": {"thread_id": thread_id}}

    for turn in turns:
        print(f"\n  USER: {turn}")
        before = len(graph.get_state(config).values.get("messages", []))
        state = graph.invoke({"messages": [HumanMessage(content=turn)]}, config)
        # Print every reply added this turn - a turn may now involve more than
        # one specialist, so the last message alone no longer tells the story.
        for message in state["messages"][before + 1:]:
            print(f"  [{getattr(message, 'name', '?')}] {message.content}")
        print(f"     (verified={state['verified']}, customer_id={state['customer_id']})")


if __name__ == "__main__":
    # Explicit, rather than a side effect of importing graph.py, so that tests
    # can point db.DB_PATH at a temp file before any table exists.
    db.init_db()
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

    conversation("demo-6", "6. PHASE 4: two specialists in one turn (policy, then refund)", [
        "rohit.v@example.com",
        "my action camera ORD1003 arrived damaged, I want a refund",
    ])

    conversation("demo-7", "7. PHASE 4: auto-approved refund, then the same refund again", [
        "ananya.s@example.com",
        "please refund ORD1006",
        "please refund ORD1006",
    ])

    conversation("demo-8", "8. PHASE 4: only delivered orders are refundable", [
        "meera.n@example.com",
        "refund ORD1004 please",
    ])
