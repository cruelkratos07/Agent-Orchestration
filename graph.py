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


def build_graph():
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
    return builder.compile(checkpointer=InMemorySaver())


graph = build_graph()
