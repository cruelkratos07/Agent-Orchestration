"""Support workflow topology and checkpoint configuration.

    START -> verify -> (verified?) -> supervisor -> order_lookup --+
                          |                     |-> policy -------+
                          |                     |-> refund -------+
                          |                     |                 |
                          |                     +<----------------+
                          |                     |
                          |                     +-- FINISH --> END
                          +-- not verified --> END (asks for identity)

The verification node is the only edge out of START.
"""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
import db
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
    # Returning to the supervisor allows several specialists in one turn.
    builder.add_edge("order_lookup", "supervisor")
    builder.add_edge("policy", "supervisor")
    builder.add_edge("refund", "supervisor")

    # A checkpointer persists state per thread_id, so a caller passes only the
    # new message instead of re-threading the whole state dict. It is also a
    # hard requirement for review: interrupt() pauses the graph by writing a
    # checkpoint, and there is nothing to resume from without one.
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def build_durable_graph(sqlite_path):
    """
    A graph whose pauses outlive the process, on whichever store is configured.

    Postgres when DATABASE_URL is set - which is what a deployment wants, and
    what lets several API processes share one escalation queue. SQLite
    otherwise, so a clone still runs with nothing installed.

    Returns (compiled_graph, checkpointer). The checkpointer comes back because
    the escalation queue enumerates threads, and only it knows which exist.
    """
    if db.DATABASE_URL:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection

        connection = Connection.connect(
            db.DATABASE_URL, autocommit=True, prepare_threshold=0
        )
        checkpointer = PostgresSaver(connection)
        checkpointer.setup()          # creates its tables; safe to re-run
        return build_graph(checkpointer), checkpointer

    return build_sqlite_graph(sqlite_path)


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

    The API calls this across registered threads to build the review queue.
    """
    snapshot = compiled_graph.get_state(config)
    for task in snapshot.tasks:
        for pending in task.interrupts:
            return pending.value
    return None


graph = build_graph()
