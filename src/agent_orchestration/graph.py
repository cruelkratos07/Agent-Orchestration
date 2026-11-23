"""Build the support graph and open its checkpoint store."""

from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StateSnapshot

from agent_orchestration import db
from agent_orchestration.agents import (
    order_lookup_node,
    policy_node,
    refund_node,
    supervisor_node,
)
from agent_orchestration.state import AgentState
from agent_orchestration.verify import route_after_verify, verify_node

SPECIALISTS = {
    "order_lookup": order_lookup_node,
    "policy": policy_node,
    "refund": refund_node,
}


def route_from_supervisor(state: AgentState) -> str:
    destination = state.get("next")
    return destination if destination in SPECIALISTS else END


def build_graph(checkpointer=None):
    """Compile the workflow with in-memory checkpoints by default."""
    builder = StateGraph(AgentState)
    builder.add_node("verify", verify_node)
    builder.add_node("supervisor", supervisor_node)

    for name, node in SPECIALISTS.items():
        builder.add_node(name, node)
        builder.add_edge(name, "supervisor")

    builder.add_edge(START, "verify")
    builder.add_conditional_edges("verify", route_after_verify, ["supervisor", END])
    builder.add_conditional_edges(
        "supervisor", route_from_supervisor, [*SPECIALISTS, END]
    )
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


@contextmanager
def open_checkpointer(sqlite_path: Path):
    """Open the configured LangGraph saver for the lifetime of the API."""
    if db.DATABASE_URL:
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(db.DATABASE_URL) as checkpointer:
            checkpointer.setup()
            yield checkpointer
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(sqlite_path)) as checkpointer:
            yield checkpointer


def list_thread_ids(checkpointer):
    """Return distinct thread IDs in checkpoint order, newest first."""
    return dict.fromkeys(
        checkpoint.config["configurable"]["thread_id"]
        for checkpoint in checkpointer.list(None)
    )


def pending_approval(snapshot: StateSnapshot):
    """Return the active refund interrupt payload, if one exists."""
    return snapshot.interrupts[0].value if snapshot.interrupts else None
