"""
Entry point. Simulates multi-turn conversations to show the
verification gate and the security boundary working.
"""

from langchain_core.messages import HumanMessage
from graph import graph
from agents import TEST_MODE

EMPTY = {"messages": [], "next": "", "customer_id": None,
         "customer_name": None, "verified": False}


def conversation(title, turns):
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    state = dict(EMPTY)

    for turn in turns:
        print(f"\n  USER: {turn}")
        state["messages"] = list(state["messages"]) + [HumanMessage(content=turn)]
        state = graph.invoke(state)
        # print only what was added this turn
        for msg in state["messages"]:
            if getattr(msg, "name", None) and msg not in turns:
                pass
        last = state["messages"][-1]
        print(f"  [{getattr(last,'name','?')}] {last.content}")
        print(f"     (verified={state['verified']}, customer_id={state['customer_id']})")


if __name__ == "__main__":
    print(f"Running in {'TEST MODE' if TEST_MODE else 'LIVE MODE'}")

    conversation("1. Unverified user is blocked, then verifies, then gets data", [
        "Where is my order ORD1002?",
        "ananya.s@example.com",
        "Where is my order ORD1002?",
    ])

    conversation("2. SECURITY: verified customer 1 tries to read customer 2's order", [
        "1",
        "Show me order ORD1003",
    ])

    conversation("3. Listing all orders on the account", [
        "rohit.v@example.com",
        "what orders do I have?",
    ])

    conversation("4. Policy question after verifying by phone", [
        "+91-99001 12233",
        "what's the policy on a damaged item?",
    ])
