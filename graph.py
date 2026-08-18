"""
GRAPH — Phase 2 wiring.

    START -> verify -> (verified?) -> supervisor -> specialist -> END
                          |
                          +-- not verified --> END (asks for identity)

The verify node sits in front of EVERYTHING. There is no edge from
START that skips it, which is what makes the guarantee structural:
an order tool cannot be reached without passing verification first.
"""

from langgraph.graph import StateGraph, START, END
from state import AgentState
from agents import supervisor_node, order_lookup_node, policy_node
from verify import verify_node

import db


def route_after_verify(state: AgentState) -> str:
    return "supervisor" if state.get("verified") else END


def route_from_supervisor(state: AgentState) -> str:
    dest = state["next"]
    if dest == "order_lookup":
        return "order_lookup"
    if dest == "policy":
        return "policy"
    return END


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("verify", verify_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("order_lookup", order_lookup_node)
    builder.add_node("policy", policy_node)

    builder.add_edge(START, "verify")
    builder.add_conditional_edges(
        "verify", route_after_verify,
        {"supervisor": "supervisor", END: END},
    )
    builder.add_conditional_edges(
        "supervisor", route_from_supervisor,
        {"order_lookup": "order_lookup", "policy": "policy", END: END},
    )
    builder.add_edge("order_lookup", END)
    builder.add_edge("policy", END)

    return builder.compile()


db.init_db()
graph = build_graph()
