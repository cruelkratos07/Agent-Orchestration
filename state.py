"""Shared state for the support workflow.

Only ``verify_node`` writes ``customer_id``. Other nodes and model output must
not change it.
"""

from typing import Annotated, Optional, Sequence, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Full conversation history. `add_messages` appends rather than overwrites.
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Routing decision made by the supervisor each turn.
    next: str

    # None until verify_node confirms the customer against the database.
    customer_id: Optional[int]
    customer_name: Optional[str]

    # True once verification succeeds. The graph refuses to route to any
    # specialist while this is False.
    verified: bool


def last_user_text(state) -> str:
    """
    The most recent thing the USER actually said.

    Why this exists: verify_node appends an AIMessage ("you're verified"),
    so `messages[-1]` is not the user's question on any turn where
    verification just succeeded. A node that reads messages[-1] would
    classify the confirmation text instead of the request, and silently
    drop a message like "I'm customer 1, where is ORD1002?".
    """
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            return message.content
    return ""


# Specialist node name -> the route that reaches it. Used to work out what has
# already run during the current turn.
SPECIALIST_ROUTES = {
    "order_agent": "order_lookup",
    "policy_agent": "policy",
    "refund_agent": "refund",
}


def messages_this_turn(state) -> list:
    """Messages appended since the user's most recent message."""
    messages = list(state["messages"])
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1:]
    return messages


def specialists_used(state) -> list:
    """
    Which specialists have already answered during THIS turn, in order.

    Derived from the message history rather than counted in a state field.
    That is deliberate: the graph has a checkpointer, so a counter stored in
    state persists across turns on the same thread_id and would have to be
    reset at the start of every turn. Forget the reset and turn 2 of every
    conversation starts at the step cap and does nothing. Deriving the value
    from messages-since-the-last-HumanMessage makes that bug impossible,
    because the window resets itself.
    """
    used = []
    for message in messages_this_turn(state):
        route = SPECIALIST_ROUTES.get(getattr(message, "name", None))
        if route:
            used.append(route)
    return used
