"""
Tests for the model layer.

No API key is used. A stub chat model stands in for Claude, which means the
LIVE paths - tool calling, fuzzy order resolution, grounded policy answers,
fallback-on-failure - are all actually exercised in CI rather than only when
someone happens to run with a key.

The most important test in this file is
test_tool_call_carrying_another_customer_id_is_overridden. Everything else is
behaviour; that one is the security claim.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

import db
import llm
from agents import order_lookup_node, policy_node, supervisor_node


class StubModel:
    """
    Minimal stand-in for a chat model.

    Only bind_tools() and invoke() are used by llm.py, so this is the whole
    surface. Scripted replies are returned in order; `seen` records the message
    lists it was called with, and `bound_tools` records what was bound.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        if not self.replies:
            raise AssertionError("StubModel ran out of scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


@pytest.fixture
def model():
    """Install a stub model, and always uninstall it afterwards."""
    installed = []

    def install(*replies):
        stub = StubModel(*replies)
        llm.use_model(stub)
        installed.append(stub)
        return stub

    yield install
    llm.use_model(None)


# --------------------------------------------------------------------
# the security claim
# --------------------------------------------------------------------

def test_tool_call_carrying_another_customer_id_is_overridden(model):
    """
    The model asks for customer 2's order while serving customer 1.

    This is prompt injection, a confused model, or a future schema change. The
    injected customer_id must win, so the tool reads customer 1's account and
    finds nothing - not customer 2's camera.
    """
    stub = model(
        tool_call("check_order_status", {"order_id": "ORD1003", "customer_id": 2}),
        AIMessage(content="I could not find that order on your account."),
    )
    answer, trace = llm.run_order_tools("show me ORD1003", customer_id=1)

    # The tool ran against customer 1, so ORD1003 (customer 2's) was not found.
    observation = next(
        m.content for m in stub.seen[-1] if m.__class__.__name__ == "ToolMessage"
    )
    assert "No order ORD1003 found on this account" in observation
    assert "Ultra HD Action Camera" not in observation
    assert answer
    # The trace never records customer_id, because the model never supplied it.
    assert trace == [{"tool": "check_order_status", "args": {"order_id": "ORD1003"}}]


def test_customer_id_is_absent_from_the_bound_schema(model):
    stub = model(AIMessage(content="done"))
    llm.run_order_tools("anything", customer_id=1)
    for tool in stub.bound_tools:
        assert "customer_id" not in tool.args, tool.name


def test_model_cannot_name_an_order_it_was_not_offered(model):
    """
    resolve_order_reference validates the answer against the customer's own
    orders. A model naming ORD1003 while serving customer 1 must not get it.
    """
    model(AIMessage(content="ORD1003"))
    resolved = llm.resolve_order_reference("where is my camera", customer_id=1)
    assert resolved != "ORD1003"
    assert resolved is None or resolved in {o["order_id"] for o in db.list_orders(1)}


# --------------------------------------------------------------------
# tool calling
# --------------------------------------------------------------------

def test_tool_loop_feeds_observations_back_and_returns_the_answer(model):
    stub = model(
        tool_call("list_my_orders", {}),
        AIMessage(content="You have three orders; the keyboard is still processing."),
    )
    answer, trace = llm.run_order_tools("what have I ordered?", customer_id=1)
    assert "three orders" in answer
    assert trace == [{"tool": "list_my_orders", "args": {}}]
    assert any(m.__class__.__name__ == "ToolMessage" for m in stub.seen[-1])


def test_unknown_tool_name_does_not_crash_the_turn(model):
    model(
        tool_call("delete_everything", {"x": 1}),
        AIMessage(content="Sorry, I could not do that."),
    )
    answer, trace = llm.run_order_tools("do something odd", customer_id=1)
    assert answer
    assert trace == []          # nothing was executed


def test_tool_loop_is_bounded(model):
    """A model that only ever calls tools must not loop forever."""
    replies = [tool_call("list_my_orders", {}, f"c{i}") for i in range(llm.MAX_TOOL_TURNS + 2)]
    model(*replies)
    answer, trace = llm.run_order_tools("loop please", customer_id=1)
    assert "name the order ID" in answer
    assert len(trace) == llm.MAX_TOOL_TURNS


# --------------------------------------------------------------------
# fuzzy order references
# --------------------------------------------------------------------

def test_model_resolves_a_description_to_an_order(model):
    model(AIMessage(content="ORD1001"))
    assert llm.resolve_order_reference("where are my earbuds?", customer_id=1) == "ORD1001"


def test_explicit_order_id_still_works_without_a_model():
    assert llm.resolve_order_reference("where is ORD1002", customer_id=1) == "ORD1002"


def test_recency_words_resolve_without_a_model():
    """"my last order" is answerable from the DB alone - newest first."""
    newest = db.list_orders(2)[0]["order_id"]
    assert llm.resolve_order_reference("refund my last order", customer_id=2) == newest


def test_no_model_and_no_order_id_gives_none():
    assert llm.resolve_order_reference("hello there", customer_id=1) is None


# --------------------------------------------------------------------
# grounded policy answers
# --------------------------------------------------------------------

def test_policy_answer_is_grounded_in_the_order(model):
    stub = model(AIMessage(content="ORD1006 was delivered on 19 July, so you are inside the window."))
    order = db.get_order("ORD1006", 1)
    answer = llm.policy_answer("can I still return this?", "canned rule", order)
    assert "19 July" in answer
    prompt = stub.seen[-1][0].content
    assert "ORD1006" in prompt and "canned rule" in prompt


def test_policy_answer_without_an_order_stays_canned(model):
    model()     # no replies scripted: the model must not be called at all
    assert llm.policy_answer("what is the policy?", "canned rule", None) == "canned rule"


# --------------------------------------------------------------------
# degradation: never worse than the deterministic path
# --------------------------------------------------------------------

def test_routing_falls_back_to_keywords_when_the_api_fails(model):
    model(RuntimeError("503 overloaded"))
    assert llm.route("where is my order ORD1002", []) == "order_lookup"


def test_order_resolution_falls_back_when_the_api_fails(model):
    model(RuntimeError("timeout"))
    assert llm.resolve_order_reference("where is ORD1002", customer_id=1) == "ORD1002"


def test_policy_answer_falls_back_when_the_api_fails(model):
    model(RuntimeError("boom"))
    order = db.get_order("ORD1006", 1)
    assert llm.policy_answer("q", "canned rule", order) == "canned rule"


def test_risk_summary_falls_back_when_the_api_fails(model):
    model(RuntimeError("boom"))
    order = db.get_order("ORD1003", 2)
    assert llm.risk_summary(order, "refund it", "over the limit") == "over the limit"


def test_empty_model_reply_falls_back(model):
    model(AIMessage(content="   "))
    order = db.get_order("ORD1006", 1)
    assert llm.policy_answer("q", "canned rule", order) == "canned rule"


# --------------------------------------------------------------------
# the nodes use it
# --------------------------------------------------------------------

def test_order_node_uses_the_model_when_one_exists(model):
    model(
        tool_call("check_order_status", {"order_id": "ORD1002"}),
        AIMessage(content="Your keyboard is still processing."),
    )
    state = {"messages": [HumanMessage(content="where is my keyboard?")], "customer_id": 1}
    out = order_lookup_node(state)
    assert out["messages"][0].content == "Your keyboard is still processing."
    assert out["messages"][0].name == "order_agent"


def test_order_node_falls_back_if_the_model_raises(model):
    model(RuntimeError("503"))
    state = {"messages": [HumanMessage(content="where is ORD1002?")], "customer_id": 1}
    out = order_lookup_node(state)
    assert "ORD1002" in out["messages"][0].content


def test_policy_node_grounds_on_the_resolved_order(model):
    model(
        AIMessage(content="ORD1006"),                       # resolve_order_reference
        AIMessage(content="Delivered 19 July, still returnable."),
    )
    state = {"messages": [HumanMessage(content="can I return the speaker?")], "customer_id": 1}
    out = policy_node(state)
    assert out["messages"][0].content == "Delivered 19 July, still returnable."


def test_supervisor_uses_the_model_and_normalizes_it(model):
    model(AIMessage(content="'order_lookup'"))              # quoted, as models do
    state = {"messages": [HumanMessage(content="where is my stuff")]}
    assert supervisor_node(state)["next"] == "order_lookup"
