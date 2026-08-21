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
from langgraph.types import interrupt
from state import AgentState, last_user_text, specialists_used
import db
from tools import (
    check_order_status,
    escalation_reason,
    list_my_orders,
    lookup_return_policy,
    process_refund,
)

# The only routes the graph knows how to reach. Anything else ends the turn.
VALID_ROUTES = ("order_lookup", "policy", "refund", "FINISH")

# Backstop on how many specialists one turn may consult. The eligibility rules
# below already bound a turn, so hitting this means routing misfired - and a
# bounded wrong answer beats GraphRecursionError in a demo.
MAX_SPECIALIST_HOPS = 3

# TEST_MODE keyword routing, in priority order. Policy sits ahead of refund so
# "my camera arrived damaged, I want a refund" explains the rule before acting
# on it - which is the two-specialist sequence Phase 4 exists to demonstrate.
TEST_MODE_KEYWORDS = (
    ("policy", ("return", "refund", "policy", "exchange", "damaged")),
    ("refund", ("refund", "money back", "reimburse")),
    ("order_lookup", ("order", "track", "shipped", "delivery", "ord")),
)


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


def keyword_route(text: str, used: list) -> str:
    """TEST_MODE routing. A specialist that already answered is not eligible."""
    for route, words in TEST_MODE_KEYWORDS:
        if route in used:
            continue
        if any(word in text for word in words):
            return route
    return "FINISH"


def supervisor_node(state: AgentState) -> dict:
    """
    Decide which specialist handles the next hop of this turn.

    Phase 4: specialists now return here instead of ending the turn, so this
    runs once per hop and must be able to decide the turn is complete. Three
    independent things guarantee it terminates:

      1. A refund is terminal - nothing follows it in the same turn.
      2. A specialist that already answered this turn is not eligible again.
         With three specialists that alone bounds a turn.
      3. MAX_SPECIALIST_HOPS is a hard backstop if 1 and 2 are ever wrong.
    """
    user_text = last_user_text(state)
    text = user_text.lower()
    used = specialists_used(state)

    # A refund ends the turn. Nothing sensible follows issuing money back.
    if "refund" in used:
        return {"next": "FINISH"}

    if len(used) >= MAX_SPECIALIST_HOPS:
        return {"next": "FINISH"}

    if TEST_MODE:
        route = keyword_route(text, used)
    else:
        prompt = (
            "Classify this customer message into exactly one category:\n"
            "order_lookup - about their orders, status, tracking, delivery\n"
            "policy - about returns, refunds, exchanges, damaged items\n"
            "refund - actually issue a refund for a named order\n"
            "FINISH - the request has been fully handled, or none apply\n"
            f"Message: {user_text}\n"
            f"Already done this turn: {', '.join(used) if used else 'nothing yet'}\n"
            "Reply with only the label."
        )
        raw = llm.invoke([HumanMessage(content=prompt)]).content
        route = normalize_route(raw)

    # Never send a turn back to a specialist that has already answered it.
    # Under a loop-back topology this is what stops a misrouting LLM looping.
    if route in used:
        route = "FINISH"

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


def normalize_decision(decision) -> tuple:
    """
    Accept whatever the approver sent and reduce it to (approved, note).

    A dict is the real shape a frontend would post. A bare bool or the strings
    "approve"/"deny" are tolerated so the decision can be typed by hand in a
    demo or a test without ceremony. Anything unrecognised is treated as NOT
    approved: the safe default for something that moves money.
    """
    if isinstance(decision, dict):
        return bool(decision.get("approved")), str(decision.get("note", ""))
    if isinstance(decision, bool):
        return decision, ""
    if isinstance(decision, str):
        return decision.strip().lower() in ("approve", "approved", "yes", "y"), ""
    return False, ""


def refund_node(state: AgentState) -> dict:
    """
    Refund specialist, with human-in-the-loop approval (Phase 6).

    Identity comes from verified state, exactly as it does for order_lookup.
    The extra safety on a write is the approval gate, not a stronger identity
    check.

    ORDERING RULE - the thing that makes this correct:

      Everything before interrupt() runs a SECOND time when the graph resumes,
      because the node restarts rather than continuing from where it paused.
      Verified by spike: a counter placed above interrupt() incremented twice
      for one approval.

      So everything above the interrupt is a pure read, and the write is
      strictly below it. Put db.refund_order() above the interrupt and one
      approval issues two refunds - which the idempotent UPDATE would absorb,
      hiding the bug rather than surfacing it.
    """
    customer_id = state["customer_id"]          # verified, trusted
    text = last_user_text(state)

    order_match = re.search(r"ORD\d+", text.upper())
    if not order_match:
        return _refund_reply(
            "Which order would you like refunded? Please share the order ID."
        )
    order_id = order_match.group(0)

    # --- reads only, above the interrupt: safe to run twice ---
    order = db.get_order(order_id, customer_id)
    if order is None or order["refund_status"] == "Refunded" or \
            order["status"] not in db.REFUNDABLE_STATUSES:
        # Nothing to decide. Let the tool produce the explanation.
        return _refund_reply(
            process_refund.invoke(
                {"order_id": order_id, "customer_id": customer_id, "reason": text}
            )
        )

    reason = escalation_reason(order, text)
    if not reason:
        return _refund_reply(
            process_refund.invoke(
                {"order_id": order_id, "customer_id": customer_id, "reason": text}
            )
        )

    # --- pause the graph and wait for a person ---
    decision = interrupt(
        {
            "type": "refund_approval",
            "order_id": order["order_id"],
            "item": order["item"],
            "amount": order["amount"],
            "customer_id": customer_id,
            "customer_name": state.get("customer_name"),
            "reason": reason,
            "customer_request": text,
        }
    )
    approved, note = normalize_decision(decision)

    # --- writes only, below the interrupt ---
    if not approved:
        suffix = f" Reviewer note: {note}" if note else ""
        return _refund_reply(
            f"Refund for {order['order_id']} was reviewed and not approved "
            f"({reason}). Nothing has been charged back.{suffix}"
        )

    result = process_refund.invoke(
        {
            "order_id": order_id,
            "customer_id": customer_id,
            "approved": True,
            "reason": text,
        }
    )
    suffix = f" Approved by a reviewer ({reason}).{(' Note: ' + note) if note else ''}"
    return _refund_reply(result + suffix)


def _refund_reply(content: str) -> dict:
    return {"messages": [AIMessage(content=content, name="refund_agent")]}
