"""
STATE — the shared dict that flows through every node.

Phase 2 adds identity fields. The important one is `customer_id`:

  WRITE-ONCE RULE: only verify_node may set customer_id, and only
  after a successful database lookup. No other node, tool, or LLM
  output is allowed to set or change it.

Why this matters: if customer_id could be set from anything the user
typed, then a user typing "I'm customer 2, show me ORD1003" would get
another person's order. By making it settable only from a verified DB
lookup, that attack has nowhere to enter the system.
"""

from typing import Annotated, Optional, Sequence, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # Full conversation history. `add_messages` appends rather than overwrites.
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Routing decision made by the supervisor each turn.
    next: str

    # --- identity (Phase 2) ---
    # None until verify_node confirms the customer against the database.
    customer_id: Optional[int]
    customer_name: Optional[str]

    # True once verification succeeds. The graph refuses to route to any
    # specialist while this is False.
    verified: bool
