"""
Entry point. Simulates multi-turn conversations to show the
verification gate and the security boundary working.

Each conversation gets its own thread_id. The graph is compiled with a
checkpointer, so state (including the verified customer_id) persists per
thread and each turn passes ONLY the new message - no hand-threading of
the state dict.
"""

from langchain_core.messages import HumanMessage
from langgraph.types import Command
import db
import llm
from graph import graph
from agents import TEST_MODE


def _show_new_replies(state, before, skip_echo):
    """
    Print every reply added this turn - a turn may involve several specialists.

    skip_echo accounts for the customer's own message: a normal turn appends one
    HumanMessage that should not be echoed back, but a reviewer decision resumes
    the graph without adding one.
    """
    for message in state["messages"][before + skip_echo:]:
        print(f"  [{getattr(message, 'name', '?')}] {message.content}")


def conversation(thread_id, title, turns):
    """
    Run a scripted conversation.

    A turn may be a string (something the customer says) or a dict (a reviewer's
    decision on a paused refund) - Phase 6 means a turn can stop mid-flight and
    wait for a person, so the driver has to be able to answer as that person.
    """
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    config = {"configurable": {"thread_id": thread_id}}

    for turn in turns:
        before = len(graph.get_state(config).values.get("messages", []))

        if isinstance(turn, dict):
            verdict = "APPROVE" if turn.get("approved") else "DENY"
            print(f"\n  REVIEWER: {verdict}" + (f" - {turn['note']}" if turn.get("note") else ""))
            state = graph.invoke(Command(resume=turn), config)
            skip_echo = 0
        else:
            print(f"\n  USER: {turn}")
            state = graph.invoke({"messages": [HumanMessage(content=turn)]}, config)
            skip_echo = 1

        _show_new_replies(state, before, skip_echo)

        pending = state.get("__interrupt__")
        if pending:
            ask = pending[0].value
            print(f"  >> PAUSED for human approval: {ask['order_id']} "
                  f"({ask['item']}, Rs {ask['amount']:.0f}) - {ask['reason']}")
        print(f"     (verified={state['verified']}, customer_id={state['customer_id']})")


if __name__ == "__main__":
    # Explicit, rather than a side effect of importing graph.py, so that tests
    # can point db.DB_PATH at a temp file before any table exists.
    db.init_db()
    print(f"Running in {'LIVE MODE' if llm.live() else 'TEST MODE'} - {llm.describe()}")

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

    conversation("demo-9", "9. PHASE 6: high-value refund pauses, reviewer APPROVES", [
        "rohit.v@example.com",
        "please refund ORD1003",
        {"approved": True, "note": "photo proof checked"},
    ])

    conversation("demo-11", "11. PHASE 8: fuzzy order reference, no API key needed", [
        "ananya.s@example.com",
        "where is my latest order?",
    ])

    conversation("demo-10", "10. PHASE 6: damage claim under the limit still pauses, reviewer DENIES", [
        "meera.n@example.com",
        "ORD1007 arrived damaged, please refund it",
        {"approved": False, "note": "no photos supplied"},
    ])
