"""Resolve a demo customer once before the supervisor can route a turn."""

import re

from langchain_core.messages import AIMessage
from langgraph.graph import END

from agent_orchestration import db
from agent_orchestration.state import AgentState, last_user_text

VERIFY_PROMPT = (
    "Before I can look up order details I need to verify your identity. "
    "Please share your customer ID, registered email, or phone number."
)


def extract_identifier(text: str):
    """Pull a plausible identifier out of free text. Returns str or None."""
    # Email
    email = re.search(r"[\w\.\-\+]+@[\w\-]+\.[\w\.\-]+", text)
    if email:
        return email.group(0)

    # Phone: 10+ digits, allowing spaces/dashes/plus between them
    phone = re.search(r"\+?[\d][\d\s\-]{8,}\d", text)
    if phone:
        return phone.group(0)

    # Bare customer ID: "customer 2", "id 3", or a lone small number.
    # Deliberately narrow so an order number like ORD1002 is not
    # mistaken for a customer ID.
    cid = re.search(r"(?:customer|cust|id)\s*#?\s*(\d{1,6})\b", text, re.I)
    if cid:
        return cid.group(1)
    if re.fullmatch(r"\s*\d{1,6}\s*", text):
        return text.strip()

    return None


def verify_node(state: AgentState) -> dict:
    """Gate every turn on a verified identity."""
    # Already verified — nothing to do.
    if state.get("verified"):
        return {}

    user_text = last_user_text(state)
    identifier = extract_identifier(user_text)

    if not identifier:
        return {
            "messages": [AIMessage(content=VERIFY_PROMPT, name="verification")],
            "verified": False,
            # Stated explicitly rather than left absent, so every caller can
            # read customer_id without a KeyError. Only reachable when not
            # already verified, so this cannot clear an established identity.
            "customer_id": None,
            "customer_name": None,
        }

    customer = db.find_customer(identifier)
    if not customer:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"I couldn't find an account matching '{identifier}'. "
                        "Please check and try again with your customer ID, "
                        "email, or registered phone number."
                    ),
                    name="verification",
                )
            ],
            "verified": False,
            "customer_id": None,
            "customer_name": None,
        }

    # SUCCESS — this is the only place customer_id is ever written.
    return {
        "customer_id": customer["customer_id"],
        "customer_name": customer["name"],
        "verified": True,
        "messages": [
            AIMessage(
                content=f"Thanks {customer['name']}, you're verified.",
                name="verification",
            )
        ],
    }


def route_after_verify(state: AgentState) -> str:
    """If verification failed, end the turn. Otherwise continue to supervisor."""
    return "supervisor" if state.get("verified") else END
