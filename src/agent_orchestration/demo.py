"""Scripted conversations for the local order support demo."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent_orchestration import cache, db, llm
from agent_orchestration.graph import build_graph


def _show_new_replies(state, before, skip_echo):
    """
    Print every reply added this turn - a turn may involve several specialists.

    skip_echo accounts for the customer's own message: a normal turn appends one
    HumanMessage that should not be echoed back, but a reviewer decision resumes
    the graph without adding one.
    """
    for message in state["messages"][before + skip_echo :]:
        print(f"  [{getattr(message, 'name', '?')}] {message.content}")


def conversation(graph, title, turns):
    """
    Run a scripted conversation.

    A turn may be a string (something the customer says) or a dict (a reviewer's
    decision on a paused refund) - Phase 6 means a turn can stop mid-flight and
    wait for a person, so the driver has to be able to answer as that person.
    """
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")
    config = {"configurable": {"thread_id": title}}

    for turn in turns:
        before = len(graph.get_state(config).values.get("messages", []))

        if isinstance(turn, dict):
            verdict = "APPROVE" if turn.get("approved") else "DENY"
            print(
                f"\n  REVIEWER: {verdict}"
                + (f" - {turn['note']}" if turn.get("note") else "")
            )
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
            print(
                f"  >> PAUSED for human approval: {ask['order_id']} "
                f"({ask['item']}, Rs {ask['amount']:.0f}) - {ask['reason']}"
            )
        print(
            f"     (verified={state['verified']}, customer_id={state['customer_id']})"
        )


def run_walkthrough():
    db.init_db()
    graph = build_graph()
    print(f"Running in {'LIVE MODE' if llm.live() else 'TEST MODE'} - {llm.describe()}")
    print(f"  data: {db.describe_backend()}   cache: {cache.stats()['backend']}")

    conversation(
        graph,
        "Unverified user is blocked, then verifies, then gets data",
        [
            "Where is my order ORD1002?",
            "ananya.s@example.com",
            "Where is my order ORD1002?",
        ],
    )

    conversation(
        graph,
        "SECURITY: verified customer 1 tries to read customer 2's order",
        [
            "1",
            "Show me order ORD1003",
        ],
    )

    conversation(
        graph,
        "Listing all orders on the account",
        [
            "rohit.v@example.com",
            "what orders do I have?",
        ],
    )

    conversation(
        graph,
        "Policy question after verifying by phone",
        [
            "+91-99001 12233",
            "what's the policy on a damaged item?",
        ],
    )

    conversation(
        graph,
        "Identity AND question in a single message",
        [
            "Hi, I'm customer 1 - where is my order ORD1002?",
        ],
    )

    conversation(
        graph,
        "two specialists in one turn (policy, then refund)",
        [
            "rohit.v@example.com",
            "my action camera ORD1003 arrived damaged, I want a refund",
        ],
    )

    conversation(
        graph,
        "auto-approved refund, then the same refund again",
        [
            "ananya.s@example.com",
            "please refund ORD1006",
            "please refund ORD1006",
        ],
    )

    conversation(
        graph,
        "only delivered orders are refundable",
        [
            "meera.n@example.com",
            "refund ORD1004 please",
        ],
    )

    conversation(
        graph,
        "high-value refund pauses, reviewer APPROVES",
        [
            "rohit.v@example.com",
            "please refund ORD1003",
            {"approved": True, "note": "photo proof checked"},
        ],
    )

    conversation(
        graph,
        "fuzzy order reference, no API key needed",
        [
            "ananya.s@example.com",
            "where is my latest order?",
        ],
    )

    conversation(
        graph,
        "damage claim under the limit still pauses, reviewer DENIES",
        [
            "meera.n@example.com",
            "ORD1007 arrived damaged, please refund it",
            {"approved": False, "note": "no photos supplied"},
        ],
    )


def main():
    if db.BACKEND == "postgres" or os.getenv("SUPPORT_DB_PATH"):
        run_walkthrough()
        return

    previous_path = db.DB_PATH
    with TemporaryDirectory(prefix="order-support-") as directory:
        db.DB_PATH = Path(directory) / "support.db"
        try:
            run_walkthrough()
        finally:
            db.DB_PATH = previous_path


if __name__ == "__main__":
    main()
