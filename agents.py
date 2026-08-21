"""
NODES — supervisor + specialists.

PHASE 2 CHANGE: order_lookup_node reads customer_id from STATE, never
from the user's message. The user can type "show me customer 7's order"
all they want; the tool still gets the verified ID that verify_node
wrote after a real database lookup.

TEST_MODE: with no ANTHROPIC_API_KEY set, routing uses keyword matching
so the whole graph stays runnable and demoable without live API calls.
"""

import os
import re
from langchain_core.messages import AIMessage, HumanMessage
from state import AgentState, last_user_text
from tools import check_order_status, list_my_orders, lookup_return_policy

# The only routes the graph knows how to reach. Anything else ends the turn.
VALID_ROUTES = ("order_lookup", "policy", "FINISH")


TEST_MODE = os.environ.get("ANTHROPIC_API_KEY") is None

if not TEST_MODE:
    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model="claude-sonnet-4-6")


def normalize_route(raw: str) -> str:
    """
    Coerce an LLM's reply into one of VALID_ROUTES.

    An exact string comparison against the raw reply is not safe: the model
    may answer "'policy'", "Order_Lookup", or "The category is order_lookup".
    Each of those would fall through to END and silently drop the turn with
    no error. Here anything unrecognised becomes FINISH explicitly.
    """
    text = (raw or "").strip().strip("\"'`.").strip().lower()
    for route in VALID_ROUTES:
        if text == route.lower():
            return route
    # Tolerate a label wrapped in a sentence, e.g. "the category is policy".
    for route in VALID_ROUTES:
        if re.search(rf"\b{re.escape(route.lower())}\b", text):
            return route
    return "FINISH"


def supervisor_node(state: AgentState) -> dict:
    """Decide which specialist handles this turn."""
    user_text = last_user_text(state)
    text = user_text.lower()

    if TEST_MODE:
        if any(w in text for w in ["return", "refund", "policy", "exchange", "damaged"]):
            route = "policy"
        elif any(w in text for w in ["order", "track", "shipped", "delivery", "ord"]):
            route = "order_lookup"
        else:
            route = "FINISH"
    else:
        prompt = (
            "Classify this customer message into exactly one category:\n"
            "order_lookup - about their orders, status, tracking, delivery\n"
            "policy - about returns, refunds, exchanges, damaged items\n"
            "FINISH - anything else\n"
            f"Message: {user_text}\n"
            "Reply with only the label."
        )
        raw = llm.invoke([HumanMessage(content=prompt)]).content
        route = normalize_route(raw)

    return {"next": route}


def order_lookup_node(state: AgentState) -> dict:
    """Order specialist. Identity comes from state, NOT from user text."""
    customer_id = state["customer_id"]          # verified, trusted
    text = last_user_text(state)

    order_match = re.search(r"ORD\d+", text.upper())
    if order_match:
        result = check_order_status.invoke(
            {"order_id": order_match.group(0), "customer_id": customer_id}
        )
    else:
        result = list_my_orders.invoke({"customer_id": customer_id})

    return {"messages": [AIMessage(content=result, name="order_agent")]}


def policy_node(state: AgentState) -> dict:
    """Policy specialist. No identity needed - policy is public."""
    text = last_user_text(state)
    result = lookup_return_policy.invoke({"topic": text})
    return {"messages": [AIMessage(content=result, name="policy_agent")]}
