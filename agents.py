"""Supervisor and specialist nodes for the support workflow."""

import re

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

import db
import llm
from llm import TEST_MODE, keyword_route, normalize_route  # re-exported
from state import AgentState, last_user_text, specialists_used
from tools import (
    check_order_status,
    escalation_reason,
    list_my_orders,
    lookup_return_policy,
    process_refund,
)

# A hard cap protects the loop if a model returns repetitive routes.
MAX_SPECIALIST_HOPS = 3

VALID_ROUTES = llm.VALID_ROUTES


def supervisor_node(state: AgentState) -> dict:
    """
    Decide which specialist handles the next hop of this turn.

    Specialists return here after each hop. Three conditions terminate a turn:

      1. A refund is terminal - nothing follows it in the same turn.
      2. A specialist that already answered this turn is not eligible again.
         With three specialists that alone bounds a turn.
      3. MAX_SPECIALIST_HOPS is a hard backstop if 1 and 2 are ever wrong.
    """
    user_text = last_user_text(state)
    used = specialists_used(state)

    # A refund is terminal for the current turn.
    if "refund" in used:
        return {"next": "FINISH"}

    if len(used) >= MAX_SPECIALIST_HOPS:
        return {"next": "FINISH"}

    route = llm.route(user_text, used)

    # Never send a turn back to a specialist that has already answered it.
    # Under a loop-back topology this is what stops a misrouting LLM looping.
    if route in used:
        route = "FINISH"

    return {"next": route}


def order_lookup_node(state: AgentState) -> dict:
    """
    Order specialist. Identity comes from state, NOT from user text.

    With a model available, ``llm.run_order_tools`` lets it choose the tool and
    order while injecting ``customer_id`` from verified state.

    With no model, the previous deterministic behaviour: an explicit order ID
    if the message contains one, otherwise list everything.
    """
    customer_id = state["customer_id"]          # verified, trusted
    text = last_user_text(state)

    if llm.active_model() is not None:
        try:
            answer, _trace = llm.run_order_tools(text, customer_id)
            return {"messages": [AIMessage(content=answer, name="order_agent")]}
        except Exception:
            pass    # fall through to the deterministic path below

    order_id = llm.resolve_order_reference(text, customer_id)
    if order_id:
        result = check_order_status.invoke(
            {"order_id": order_id, "customer_id": customer_id}
        )
    else:
        result = list_my_orders.invoke({"customer_id": customer_id})

    return {"messages": [AIMessage(content=result, name="order_agent")]}


def policy_node(state: AgentState) -> dict:
    """
    Policy specialist. The rule itself is public; applying it needs the order.

    The canned rule from lookup_return_policy is always the floor. When the
    customer is verified and a specific order can be identified, the model
    applies the rule to that order's real dates and status instead of restating
    it and leaving the customer to do the arithmetic.
    """
    text = last_user_text(state)
    canned = lookup_return_policy.invoke({"topic": text})

    order = None
    customer_id = state.get("customer_id")
    if customer_id is not None:
        order_id = llm.resolve_order_reference(text, customer_id)
        if order_id:
            order = db.get_order(order_id, customer_id)

    result = llm.policy_answer(text, canned, order)
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
        return decision.get("approved") is True, str(decision.get("note", ""))
    if isinstance(decision, bool):
        return decision, ""
    if isinstance(decision, str):
        return decision.strip().lower() in ("approve", "approved", "yes", "y"), ""
    return False, ""


def refund_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    Refund specialist with human review for escalated requests.

    Identity comes from verified state, exactly as it does for order_lookup.
    The extra safety on a write is the approval gate, not a stronger identity
    check.

    LangGraph restarts this node when an interrupt resumes. Everything above
    ``interrupt`` must therefore be side-effect free; the database mutation
    stays below it.
    """
    customer_id = state["customer_id"]          # verified, trusted
    text = last_user_text(state)

    reviewed = config.get("configurable", {}).get("reviewed_action")
    order_id = reviewed["order_id"] if reviewed else llm.resolve_order_reference(text, customer_id)
    if not order_id:
        return _refund_reply(
            "Which order would you like refunded? Please share the order ID."
        )

    # --- reads only, above the interrupt: safe to run twice ---
    order = db.get_order(order_id, customer_id)
    if reviewed and (order is None or any(
        reviewed.get(key) != order.get(key) for key in ("order_id", "customer_id", "amount")
    )):
        raise ValueError("Reviewed order changed; operator reconciliation required")
    if order is None or order["refund_status"] == "Refunded" or \
            order["status"] not in db.REFUNDABLE_STATUSES:
        # Nothing to decide. Let the tool produce the explanation.
        return _refund_reply(
            process_refund.invoke(
                {"order_id": order_id, "customer_id": customer_id, "reason": text}
            )
        )

    reason = escalation_reason(order, text)
    if not reason and not reviewed:
        return _refund_reply(
            process_refund.invoke(
                {"order_id": order_id, "customer_id": customer_id, "reason": text}
            )
        )

    # --- pause the graph and wait for a person ---
    # risk_summary can run again on resume and must remain side-effect free.
    decision = interrupt(
        {
            "type": "refund_approval",
            "order_id": order["order_id"],
            "item": order["item"],
            "amount": order["amount"],
            "customer_id": customer_id,
            "customer_name": state.get("customer_name"),
            "reason": reason,
            "risk_summary": llm.risk_summary(order, text, reason),
            "customer_request": text,
        }
    )
    approved, note = normalize_decision(decision)

    # A resumed node re-resolves the order. Bind API approval to what the
    # reviewer actually saw; changed model output must never move other money.
    if isinstance(decision, dict) and "action" in decision:
        action = decision["action"]
        if any(action.get(key) != order.get(key) for key in ("order_id", "customer_id", "amount")):
            return _refund_reply("Order details changed during review. No refund was issued; request a new review.")

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
