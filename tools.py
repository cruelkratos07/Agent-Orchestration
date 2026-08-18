"""
TOOLS — now backed by the real database instead of a dict.

KEY DESIGN CHANGE FROM PHASE 1:
Every order tool takes customer_id as a required argument, and that
value is scoped into the SQL query itself. A tool physically cannot
return data belonging to a different customer, no matter what the
LLM asks it for. Security lives in the tool signature, not in a prompt
politely asking the model to behave.
"""

from langchain_core.tools import tool
import db


@tool
def check_order_status(order_id: str, customer_id: int) -> str:
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
def list_my_orders(customer_id: int) -> str:
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
    return "General policy: returns accepted within 15 days of delivery in original condition."
