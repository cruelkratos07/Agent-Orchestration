"""
THE MODEL LAYER — every place Claude is actually used, and its fallback.

Phase 8 exists because the project had exactly one LLM call in it: a routing
classifier that a dict of keywords already handled. The architecture was the
interesting part, but "where does the model earn its place?" had a thin answer.

Four jobs now, each one something code genuinely cannot do well:

  route()                  - intent routing (was already here)
  resolve_order_reference()- "where's my camera?" -> a specific order ID
  policy_answer()          - the policy rule applied to THIS order's dates
  risk_summary()           - a one-line brief for the human reviewing a refund
  answer_order_question()  - real tool calling, via bind_tools

TWO RULES THAT APPLY TO ALL OF THEM
-----------------------------------
1. EVERY function has a deterministic fallback and is never worse than it.
   If there is no API key, if the call raises, or if the model returns
   something invalid, the deterministic branch runs. The no-key demo has to
   keep working at every phase, and a flaky API must degrade rather than fail.

2. THE MODEL SUGGESTS, THE CODE VERIFIES.
   Anything the model returns that could reach the database is validated
   against a customer-scoped set first. The model cannot widen its own access
   by returning a value nobody offered it - see resolve_order_reference and
   answer_order_question.
"""

import os
import re
from datetime import date, datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import db
from tools import check_order_status, list_my_orders

TEST_MODE = os.environ.get("ANTHROPIC_API_KEY") is None
MODEL_NAME = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

_model = None


def active_model():
    """The chat model, or None when running without a key."""
    global _model
    if _model is not None:
        return _model
    if TEST_MODE:
        return None
    from langchain_anthropic import ChatAnthropic

    _model = ChatAnthropic(model=MODEL_NAME, temperature=0)
    return _model


def use_model(model):
    """Override the model. Tests inject a stub; nothing else should call this."""
    global _model
    _model = model


def _text(response) -> str:
    """Model content as a plain string, tolerating list-of-blocks content."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    return str(content).strip()


# --------------------------------------------------------------------------
# 1. Routing
# --------------------------------------------------------------------------

VALID_ROUTES = ("order_lookup", "policy", "refund", "FINISH")

TEST_MODE_KEYWORDS = (
    ("policy", ("return", "refund", "policy", "exchange", "damaged")),
    ("refund", ("refund", "money back", "reimburse")),
    ("order_lookup", ("order", "track", "shipped", "delivery", "ord")),
)


def normalize_route(raw: str) -> str:
    """
    Coerce a model reply into one of VALID_ROUTES.

    Exact comparison is unsafe: the model may answer "'policy'",
    "Order_Lookup", or "The category is order_lookup". Each of those would
    otherwise fall through to END and drop the turn with no error.
    """
    text = (raw or "").strip().strip("\"'`.").strip().lower()
    for route in VALID_ROUTES:
        if text == route.lower():
            return route
    for route in VALID_ROUTES:
        if re.search(rf"\b{re.escape(route.lower())}\b", text):
            return route
    return "FINISH"


def keyword_route(text: str, used: list) -> str:
    """Deterministic routing. A specialist that already answered is not eligible."""
    lowered = (text or "").lower()
    for route, words in TEST_MODE_KEYWORDS:
        if route in used:
            continue
        if any(word in lowered for word in words):
            return route
    return "FINISH"


def route(text: str, used: list) -> str:
    model = active_model()
    if model is None:
        return keyword_route(text, used)

    prompt = (
        "Classify this customer message into exactly one label:\n"
        "order_lookup - about their orders, status, tracking, delivery\n"
        "policy - about returns, refunds, exchanges, damaged items\n"
        "refund - actually issue a refund for a named order\n"
        "FINISH - the request has been fully handled, or none apply\n"
        f"Message: {text}\n"
        f"Already done this turn: {', '.join(used) if used else 'nothing yet'}\n"
        "Reply with only the label."
    )
    try:
        return normalize_route(_text(model.invoke([HumanMessage(content=prompt)])))
    except Exception:
        # A flaky API must degrade to keyword routing, not break the turn.
        return keyword_route(text, used)


# --------------------------------------------------------------------------
# 2. Resolving a fuzzy order reference
# --------------------------------------------------------------------------

RECENCY_WORDS = ("last", "latest", "recent", "newest", "most recent")


def _fallback_order_reference(text: str, orders: list):
    """Regex, then a recency heuristic. What the project did before Phase 8."""
    match = re.search(r"ORD\d+", (text or "").upper())
    if match:
        return match.group(0)
    lowered = (text or "").lower()
    if orders and any(word in lowered for word in RECENCY_WORDS):
        return orders[0]["order_id"]        # db.list_orders is newest-first
    return None


def resolve_order_reference(text: str, customer_id: int):
    """
    Turn "where's my camera?" into an order ID, or None.

    The order list handed to the model is ALREADY scoped by customer_id, and
    whatever the model answers is checked back against that list. So the model
    picks among rows this customer owns and cannot invent its way to another
    account's order - if it returns something not on the list, we fall back.
    """
    orders = db.list_orders(customer_id)
    allowed = {order["order_id"].upper() for order in orders}

    model = active_model()
    if model is None:
        return _fallback_order_reference(text, orders)

    catalogue = "\n".join(
        f"- {o['order_id']}: {o['item']} ({o['status']}, ordered {o['ordered_on']})"
        for o in orders
    ) or "- (no orders on this account)"

    prompt = (
        "Match the customer's message to exactly one of their orders.\n"
        f"{catalogue}\n\n"
        f'Customer said: "{text}"\n\n'
        "Reply with only the order ID, or NONE if no single order clearly matches."
    )
    try:
        answer = _text(model.invoke([HumanMessage(content=prompt)])).upper()
    except Exception:
        return _fallback_order_reference(text, orders)

    match = re.search(r"ORD\d+", answer)
    if match and match.group(0) in allowed:
        return match.group(0)
    # Model said NONE, or named something it was not offered. Either way, do
    # not trust it - fall back to the deterministic reading of the message.
    return _fallback_order_reference(text, orders)


# --------------------------------------------------------------------------
# 3. Grounded policy answers
# --------------------------------------------------------------------------

def policy_answer(text: str, canned: str, order=None, today: date = None) -> str:
    """
    The policy rule, applied to this order.

    The canned rule is always the floor: it is what gets returned with no key,
    on any error, and when there is no specific order in play. With a model and
    an order, the answer can say "delivered 22 days ago, outside the 15-day
    window" instead of restating the rule and leaving the customer to do the
    arithmetic.
    """
    model = active_model()
    if model is None or order is None:
        return canned

    today = today or datetime.now(timezone.utc).date()
    facts = [
        f"order {order['order_id']} ({order['item']})",
        f"status {order['status']}",
        f"amount Rs {order['amount']:.0f}",
        f"ordered on {order['ordered_on']}",
    ]
    if order.get("eta"):
        facts.append(f"delivery note: {order['eta']}")
    if order.get("refund_status"):
        facts.append(f"refund status: {order['refund_status']}")

    prompt = (
        "You are a support agent. Answer the customer using ONLY the policy and "
        "the order facts below. Do not invent dates, amounts or entitlements. "
        "If the facts are not enough to decide, say what you would need.\n\n"
        f"Policy: {canned}\n"
        f"Order facts: {'; '.join(facts)}\n"
        f"Today: {today.isoformat()}\n\n"
        f'Customer said: "{text}"\n\n'
        "Reply in at most two sentences."
    )
    try:
        answer = _text(model.invoke([HumanMessage(content=prompt)]))
    except Exception:
        return canned
    return answer or canned


# --------------------------------------------------------------------------
# 4. Risk summary for the human reviewer
# --------------------------------------------------------------------------

def risk_summary(order, request_text: str, reason: str) -> str:
    """One line for the reviewer. Falls back to the mechanical reason string."""
    model = active_model()
    if model is None:
        return reason

    prompt = (
        "A refund is waiting for human approval. In ONE short sentence, tell "
        "the reviewer what to weigh. Be specific and do not recommend a "
        "decision.\n\n"
        f"Order: {order['order_id']} - {order['item']}, Rs {order['amount']:.0f}, "
        f"status {order['status']}, ordered {order['ordered_on']}\n"
        f"Escalated because: {reason}\n"
        f'Customer said: "{request_text}"'
    )
    try:
        return _text(model.invoke([HumanMessage(content=prompt)])) or reason
    except Exception:
        return reason


# --------------------------------------------------------------------------
# 5. Real tool calling
# --------------------------------------------------------------------------

ORDER_TOOLS = (check_order_status, list_my_orders)
ORDER_TOOLS_BY_NAME = {tool.name: tool for tool in ORDER_TOOLS}

MAX_TOOL_TURNS = 4

ORDER_SYSTEM_PROMPT = (
    "You are the order specialist for an e-commerce support desk. Use the "
    "tools to answer questions about the customer's orders. Never ask the "
    "customer to confirm who they are - identity is already verified. Answer "
    "in at most three sentences, using only what the tools return."
)


def run_order_tools(text: str, customer_id: int):
    """
    Let the model drive the order tools.

    THE SECURITY POINT, and the reason InjectedToolArg was added in Phase 4:

      customer_id is injected here, at execution time, from verified state. It
      is absent from the schema the model sees, so the model cannot supply it -
      and if a tool call arrives carrying one anyway (prompt injection, a
      confused model, a future schema change) it is DISCARDED and overwritten.

      The model chooses which tool to call and what order to ask about. It does
      not get a vote on whose account is read.

    Returns (answer, trace) where trace lists the tool calls made, so the UI
    and the tests can see what the model actually did.
    """
    model = active_model()
    if model is None:
        raise RuntimeError("run_order_tools requires a model; callers must check")

    bound = model.bind_tools(list(ORDER_TOOLS))
    messages = [SystemMessage(content=ORDER_SYSTEM_PROMPT), HumanMessage(content=text)]
    trace = []

    for _ in range(MAX_TOOL_TURNS):
        response = bound.invoke(messages)
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            return _text(response), trace

        messages.append(response)
        for call in calls:
            tool = ORDER_TOOLS_BY_NAME.get(call["name"])
            if tool is None:
                observation = f"No such tool: {call['name']}"
            else:
                args = dict(call.get("args") or {})
                args.pop("customer_id", None)          # never trust it
                args["customer_id"] = customer_id      # inject the verified one
                trace.append({"tool": call["name"], "args": {k: v for k, v in args.items()
                                                            if k != "customer_id"}})
                observation = tool.invoke(args)
            messages.append(
                ToolMessage(content=str(observation), tool_call_id=call.get("id", ""))
            )

    # Ran out of turns. Say so rather than returning a half-answer.
    return (
        "I wasn't able to finish looking that up. Could you name the order ID?",
        trace,
    )
