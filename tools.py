"""Customer-scoped order tools and public policy lookup."""

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
import db

# Refunds at or above this value need reviewer approval.
REFUND_APPROVAL_THRESHOLD = 5000.0

# Words that make a refund a judgement call regardless of value. A damage claim
# carries fraud risk, so it goes to a human even when the amount is small.
ESCALATION_KEYWORDS = ("damaged", "broken", "faulty", "defective")


def escalation_reason(order: dict, request_text: str = "") -> str:
    """
    Why this refund needs a human, or "" if it can be auto-approved.

    Pure and side-effect free on purpose: refund_node calls this BEFORE
    interrupt(), and everything before an interrupt runs a second time when the
    graph resumes. Anything with a side effect must sit after the interrupt.
    """
    if order["amount"] >= REFUND_APPROVAL_THRESHOLD:
        return (
            f"Rs {order['amount']:.0f} is at or above the "
            f"Rs {REFUND_APPROVAL_THRESHOLD:.0f} auto-approval limit"
        )
    lowered = (request_text or "").lower()
    for word in ESCALATION_KEYWORDS:
        if word in lowered:
            return f"customer reports the item was {word}"
    return ""


@tool
def check_order_status(
    order_id: str,
    customer_id: Annotated[int, InjectedToolArg],
) -> str:
    """Look up the status of one specific order belonging to the given customer.
    Use when the customer names a specific order ID."""
    order = db.get_order(order_id, customer_id)
    if not order:
        return (
            f"No order {order_id} found on this account. "
            "Please double-check the order ID."
        )
    parts = [
        f"Order {order['order_id']} ({order['item']}, Rs {order['amount']:.0f})",
        f"status = {order['status']}",
    ]
    if order["carrier"]:
        parts.append(f"carrier = {order['carrier']}")
    if order["eta"]:
        parts.append(f"ETA = {order['eta']}")
    return ", ".join(parts) + "."


@tool
def list_my_orders(customer_id: Annotated[int, InjectedToolArg]) -> str:
    """List all orders on the given customer's account, newest first.
    Use when the customer asks about their orders without naming one."""
    orders = db.list_orders(customer_id)
    if not orders:
        return "There are no orders on this account yet."
    lines = [
        f"- {o['order_id']}: {o['item']} (Rs {o['amount']:.0f}) - {o['status']}"
        for o in orders
    ]
    return f"Found {len(orders)} order(s):\n" + "\n".join(lines)


@tool
def lookup_return_policy(topic: str) -> str:
    """Look up the store's return/refund/exchange policy for a topic.
    Does not require customer identity - policy is the same for everyone."""
    policies = {
        "refund window": "Refunds are accepted within 15 days of delivery, unused and in original packaging.",
        "damaged item": "Damaged items can be returned within 7 days of delivery with photo proof, no questions asked.",
        "exchange": "Exchanges are allowed once per order within 15 days, subject to stock availability.",
        "cancelled": "Cancelled orders are refunded to the original payment method within 5-7 business days.",
    }
    topic_key = topic.lower().strip()
    for key, value in policies.items():
        if key in topic_key or topic_key in key:
            return value

    # The topic arrives as the customer's whole sentence, so an exact phrase
    # match almost never lands: "my camera arrived damaged, I want a refund"
    # contains neither "damaged item" nor "refund window". Fall back to single
    # keywords, most specific first - "damaged" must win over "refund" here,
    # or a damaged-item claim gets answered with the generic refund window.
    keyword_priority = (
        ("damaged", "damaged item"),
        ("exchange", "exchange"),
        ("cancel", "cancelled"),
        ("refund", "refund window"),
        ("return", "refund window"),
    )
    for keyword, key in keyword_priority:
        if keyword in topic_key:
            return policies[key]

    return "General policy: returns accepted within 15 days of delivery in original condition."


@tool
def process_refund(
    order_id: str,
    customer_id: Annotated[int, InjectedToolArg],
    approved: Annotated[bool, InjectedToolArg] = False,
    reason: str = "",
) -> str:
    """Issue a refund for one of this customer's delivered orders.
    Use when the customer asks for their money back on a specific order."""
    order = db.get_order(order_id, customer_id)
    if not order:
        return (
            f"No order {order_id} found on this account, so there is nothing "
            "to refund. Please double-check the order ID."
        )

    if order["refund_status"] == "Refunded":
        return (
            f"Order {order['order_id']} has already been refunded "
            f"(on {order['refunded_at']}). No further action taken."
        )

    if order["status"] not in db.REFUNDABLE_STATUSES:
        return (
            f"Order {order['order_id']} is {order['status'].lower()}, and only "
            f"delivered orders can be refunded. No refund was issued."
        )

    # Belt and braces. refund_node decides escalation and obtains approval
    # before calling this, but the tool refuses any escalated write on its own
    # authority anyway. `approved` is an InjectedToolArg, so an LLM cannot set
    # it - the same reasoning that protects customer_id.
    if escalation_reason(order, reason) and approved is not True:
        return (
            f"Refund of Rs {order['amount']:.0f} for {order['order_id']} is "
            f"outside automatic approval policy ({escalation_reason(order, reason)}), "
            "so it needs a human to approve it. Nothing has been "
            "charged back yet and the order is unchanged."
        )

    if db.refund_order(order_id, customer_id) == 0:
        return (
            f"Order {order['order_id']} could not be refunded - it may have "
            "just been refunded already. Nothing was changed."
        )

    return (
        f"Refund of Rs {order['amount']:.0f} issued for {order['order_id']} "
        f"({order['item']}). It will reach the original payment method in "
        "5-7 business days."
    )
