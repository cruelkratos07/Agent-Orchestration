"""
GRAPH — Phase 2 wiring.

    START -> verify -> (verified?) -> supervisor -> order_lookup --+
                          |                     |-> policy -------+
                          |                     |-> refund -------+
                          |                     |                 |
                          |                     +<----------------+
                          |                     |
                          |                     +-- FINISH --> END
                          +-- not verified --> END (asks for identity)

The verify node sits in front of EVERYTHING. There is no edge from
START that skips it, which is what makes the guarantee structural:
an order tool cannot be reached without passing verification first.
"""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from state import AgentState
from agents import supervisor_node, order_lookup_node, policy_node, refund_node
from verify import verify_node


def route_after_verify(state: AgentState) -> str:
    return "supervisor" if state.get("verified") else END


def route_from_supervisor(state: AgentState) -> str:
    # supervisor_node has already normalized this to a known route.
    dest = state.get("next")
    if dest in ("order_lookup", "policy", "refund"):
        return dest
    return END


def build_graph(checkpointer=None):
    """
    Compile the graph.

    checkpointer defaults to InMemorySaver, which is right for the CLI demo and
    for tests. The API server passes a SqliteSaver instead: a web app that
    forgets every pending refund approval when it restarts is not much of a
    human-in-the-loop system.
    """
    builder = StateGraph(AgentState)

    builder.add_node("verify", verify_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("order_lookup", order_lookup_node)
    builder.add_node("policy", policy_node)
    builder.add_node("refund", refund_node)

    builder.add_edge(START, "verify")
    builder.add_conditional_edges(
        "verify", route_after_verify,
        {"supervisor": "supervisor", END: END},
    )
    builder.add_conditional_edges(
        "supervisor", route_from_supervisor,
        {
            "order_lookup": "order_lookup",
            "policy": "policy",
            "refund": "refund",
            END: END,
        },
    )
    # Phase 4: specialists hand control BACK to the supervisor instead of
    # ending the turn, so one user message can require several specialists.
    # supervisor_node is what decides the turn is over - see its docstring for
    # the three rules that guarantee this loop terminates.
    builder.add_edge("order_lookup", "supervisor")
    builder.add_edge("policy", "supervisor")
    builder.add_edge("refund", "supervisor")

    # A checkpointer persists state per thread_id, so a caller passes only the
    # new message instead of re-threading the whole state dict. It is also a
    # hard requirement for Phase 6: interrupt() pauses the graph by writing a
    # checkpoint, and there is nothing to resume from without one.
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def build_sqlite_graph(path):
    """
    A graph whose pauses outlive the process.

    Returns (compiled_graph, checkpointer). The checkpointer is handed back
    because the escalation queue needs to enumerate threads, and only the
    checkpointer knows which threads exist.

    check_same_thread=False because the API serves sync endpoints from a thread
    pool. Callers must serialise access themselves - see api.GRAPH_LOCK.

    Imported lazily so langgraph-checkpoint-sqlite stays an API-only dependency
    and `pip install -r requirements.txt && python main.py` keeps working with
    nothing extra.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    return build_graph(checkpointer), checkpointer


def list_thread_ids(checkpointer):
    """Every thread the checkpointer has seen, most recently written first."""
    seen = []
    for checkpoint in checkpointer.list(None):
        thread_id = checkpoint.config["configurable"]["thread_id"]
        if thread_id not in seen:
            seen.append(thread_id)
    return seen


def pending_approval(compiled_graph, config):
    """
    The approval a thread is waiting on, or None.

    Phase 7's escalation queue is this called across threads. Kept here rather
    than in the frontend so the payload shape has one owner.
    """
    snapshot = compiled_graph.get_state(config)
    for task in snapshot.tasks:
        for pending in task.interrupts:
            return pending.value
    return None


graph = build_graph()
