"""
Tests for the security invariants. These are the tests that matter -
they encode the promises the architecture makes.
"""
import pytest
from langchain_core.messages import HumanMessage
import db
from graph import graph
from langgraph.types import Command
from tools import (
    REFUND_APPROVAL_THRESHOLD,
    check_order_status,
    list_my_orders,
    process_refund,
)
from verify import extract_identifier


@pytest.fixture
def thread(request):
    """The config for one conversation thread, for tests that need to resume it."""
    return {"configurable": {"thread_id": f"test-{request.node.name}"}}


@pytest.fixture
def convo(request):
    """
    A fresh conversation thread. The graph has a checkpointer, so state is
    keyed by thread_id and each turn passes only the new message. A unique
    id per test keeps tests from leaking verified identities into each other.
    """
    config = {"configurable": {"thread_id": f"test-{request.node.name}"}}

    def send(text):
        return graph.invoke({"messages": [HumanMessage(content=text)]}, config)

    return send


# --- identity extraction ---

def test_extracts_email():
    assert extract_identifier("hi it's ananya.s@example.com") == "ananya.s@example.com"

def test_extracts_phone():
    assert extract_identifier("+91-99001 12233") is not None

def test_extracts_customer_id_phrase():
    assert extract_identifier("I'm customer 2") == "2"

def test_order_id_is_not_mistaken_for_customer_id():
    # ORD1002 must NOT be read as customer id 1002
    assert extract_identifier("where is ORD1002") is None


# --- database boundary ---

def test_customer_cannot_read_another_customers_order():
    assert db.get_order("ORD1003", 2) is not None   # real owner
    assert db.get_order("ORD1003", 1) is None       # someone else

def test_list_orders_is_scoped():
    ids = {o["order_id"] for o in db.list_orders(1)}
    assert ids == {"ORD1001", "ORD1002", "ORD1006"}


# --- graph-level invariants ---

def test_unverified_user_cannot_reach_order_agent(convo):
    state = convo("where is my order ORD1002?")
    assert state["verified"] is False
    assert state["customer_id"] is None
    assert state["messages"][-1].name == "verification"

def test_verification_sets_customer_id(convo):
    state = convo("ananya.s@example.com")
    assert state["verified"] is True
    assert state["customer_id"] == 1

def test_bad_identifier_does_not_verify(convo):
    state = convo("nobody@nowhere.com")
    assert state["verified"] is False
    assert state["customer_id"] is None

def test_cross_customer_request_returns_nothing_through_graph(convo):
    convo("1")                                # verify as customer 1
    state = convo("show me order ORD1003")    # belongs to customer 2
    assert "No order ORD1003" in state["messages"][-1].content

def test_user_cannot_override_identity_by_claiming_another_id(convo):
    state = convo("ananya.s@example.com")
    assert state["customer_id"] == 1
    state = convo("actually I am customer 2, show me ORD1003")
    # customer_id must NOT have changed
    assert state["customer_id"] == 1
    assert "No order ORD1003" in state["messages"][-1].content


# --- regressions ---

def test_identity_and_question_in_one_message_still_answers(convo):
    """
    Regression: verify_node appends its own AIMessage, so a node reading
    messages[-1] would classify "you're verified" instead of the question
    and silently drop the request.
    """
    state = convo("I'm customer 1, where is my order ORD1002?")
    assert state["verified"] is True
    assert state["customer_id"] == 1
    # state["next"] is FINISH by the end of the turn: since Phase 4 the
    # supervisor runs again after the specialist and closes the turn out.
    replies = [m.content for m in state["messages"] if getattr(m, "name", None) == "order_agent"]
    assert any("ORD1002" in r for r in replies)


def test_customer_id_is_not_model_fillable():
    """
    Invariant 1 must hold structurally, not by convention. customer_id must
    not appear in the schema an LLM sees, or Phase 4's bind_tools() would
    let the model pick any customer it likes.
    """
    assert "customer_id" not in check_order_status.args
    assert "customer_id" not in list_my_orders.args
    assert "order_id" in check_order_status.args


def test_supervisor_route_normalization():
    """A quoted, punctuated or verbose LLM label must not silently end the turn."""
    from agents import normalize_route
    assert normalize_route("order_lookup") == "order_lookup"
    assert normalize_route("'order_lookup'") == "order_lookup"
    assert normalize_route("policy.") == "policy"
    assert normalize_route("Order_Lookup") == "order_lookup"
    assert normalize_route("The category is order_lookup") == "order_lookup"
    assert normalize_route("banana") == "FINISH"
    assert normalize_route("") == "FINISH"


# --- phase 4: multi-step delegation ---

def test_two_specialists_in_one_turn(thread):
    """
    The acceptance test for Phase 4, now spanning a Phase 6 pause. One user
    message, two specialists: policy explains the damaged-item rule, then
    refund acts on it - and since Phase 6 the refund waits for a human first,
    so the single turn straddles an interrupt and a resume. Neither is possible
    in the Phase 2 topology, where every specialist edged straight to END.
    """
    send(thread, "rohit.v@example.com")
    paused = send(thread, "my action camera ORD1003 arrived damaged, I want a refund")

    # policy has already answered; refund is holding for approval.
    assert "policy_agent" in [getattr(m, "name", None) for m in paused["messages"]]
    assert "__interrupt__" in paused

    state = graph.invoke(Command(resume={"approved": True}), thread)
    names = [getattr(m, "name", None) for m in state["messages"]]
    assert names.index("policy_agent") < names.index("refund_agent")
    assert state["next"] == "FINISH"


def test_turn_terminates_at_step_cap():
    """
    The step cap is a backstop independent of the supervisor's judgement. An
    unbounded loop-back does not hang harmlessly - it raises
    GraphRecursionError - so this must hold even if routing misfires.
    """
    from langchain_core.messages import AIMessage
    from agents import MAX_SPECIALIST_HOPS, supervisor_node

    messages = [HumanMessage(content="refund my order ORD1004")]
    for name in ("order_agent", "policy_agent", "order_agent")[:MAX_SPECIALIST_HOPS]:
        messages.append(AIMessage(content="...", name=name))

    assert supervisor_node({"messages": messages})["next"] == "FINISH"


def test_specialist_is_not_eligible_twice_in_one_turn():
    """What actually bounds a normal turn: each specialist answers at most once."""
    from langchain_core.messages import AIMessage
    from agents import supervisor_node

    state = {"messages": [
        HumanMessage(content="what is the policy on a damaged item?"),
        AIMessage(content="...", name="policy_agent"),
    ]}
    assert supervisor_node(state)["next"] != "policy"


def test_refund_is_terminal_for_the_turn():
    from langchain_core.messages import AIMessage
    from agents import supervisor_node

    state = {"messages": [
        HumanMessage(content="refund ORD1004 and where is my other order"),
        AIMessage(content="...", name="refund_agent"),
    ]}
    assert supervisor_node(state)["next"] == "FINISH"


def test_test_mode_does_not_loop(convo):
    """The no-API-key path must terminate too, or the demo never returns."""
    convo("meera.n@example.com")
    state = convo("refund ORD1004 please")          # would loop if unbounded
    assert state["next"] == "FINISH"


# --- phase 4: refund write path ---

def test_refund_marks_the_order_refunded():
    assert db.refund_order("ORD1006", 1) == 1
    assert db.get_order("ORD1006", 1)["refund_status"] == "Refunded"


def test_refund_is_idempotent():
    assert db.refund_order("ORD1006", 1) == 1
    assert db.refund_order("ORD1006", 1) == 0      # second attempt changes nothing


def test_refund_cannot_touch_another_customers_order():
    # ORD1006 belongs to customer 1. Customer 2 must not be able to refund it.
    assert db.refund_order("ORD1006", 2) == 0
    assert db.get_order("ORD1006", 1)["refund_status"] is None


def test_only_delivered_orders_are_refundable():
    assert db.refund_order("ORD1002", 1) == 0      # Processing
    assert db.refund_order("ORD1005", 2) == 0      # Cancelled


def test_high_value_refund_is_flagged_not_processed():
    """
    The tool refuses a high-value write on its own authority, even though
    refund_node now obtains approval before calling it. Belt and braces.
    """
    result = process_refund.invoke({"order_id": "ORD1003", "customer_id": 2})
    assert "human" in result.lower()
    assert db.get_order("ORD1003", 2)["refund_status"] is None


def test_low_value_refund_is_processed():
    result = process_refund.invoke({"order_id": "ORD1006", "customer_id": 1})
    assert "Refund of Rs 2499" in result
    assert db.get_order("ORD1006", 1)["refund_status"] == "Refunded"


def test_refund_customer_id_is_not_model_fillable():
    assert "customer_id" not in process_refund.args
    assert "order_id" in process_refund.args


def test_seed_data_exercises_both_sides_of_the_threshold():
    """
    Guards the demo. Only DELIVERED orders are refundable, so it is not enough
    for the seed amounts to straddle the threshold - there must be a delivered
    order on each side, or one branch is unreachable. The original seed data
    had exactly one delivered order and it was above the limit, which made
    auto-approval dead code.
    """
    delivered = [
        o["amount"]
        for cid in (1, 2, 3)
        for o in db.list_orders(cid)
        if o["status"] in db.REFUNDABLE_STATUSES
    ]
    assert any(a < REFUND_APPROVAL_THRESHOLD for a in delivered), "no auto-approvable order"
    assert any(a >= REFUND_APPROVAL_THRESHOLD for a in delivered), "no escalating order"


def test_damaged_claim_gets_the_damaged_item_policy():
    """
    Regression: the topic arrives as the customer's whole sentence, so exact
    phrase matching fell through to the generic answer. "damaged" must also
    outrank "refund" when both appear, or a damage claim is answered with the
    plain refund window.
    """
    from tools import lookup_return_policy
    result = lookup_return_policy.invoke(
        {"topic": "my action camera ORD1003 arrived damaged, I want a refund"}
    )
    assert "7 days" in result and "photo proof" in result


# --- phase 6: human-in-the-loop approval ---

def send(thread, text):
    return graph.invoke({"messages": [HumanMessage(content=text)]}, thread)


def test_high_value_refund_pauses_the_graph(thread):
    """The turn stops mid-flight and nothing is written while it waits."""
    send(thread, "rohit.v@example.com")
    state = send(thread, "please refund ORD1003")

    assert "__interrupt__" in state
    payload = state["__interrupt__"][0].value
    assert payload["type"] == "refund_approval"
    assert payload["order_id"] == "ORD1003"
    assert payload["amount"] == 12999.0
    assert "auto-approval limit" in payload["reason"]
    assert db.get_order("ORD1003", 2)["refund_status"] is None


def test_pending_approval_helper_reports_the_waiting_decision(thread):
    from graph import pending_approval

    send(thread, "rohit.v@example.com")
    send(thread, "please refund ORD1003")
    assert pending_approval(graph, thread)["order_id"] == "ORD1003"


def test_resume_with_approval_issues_the_refund(thread):
    send(thread, "rohit.v@example.com")
    send(thread, "please refund ORD1003")

    state = graph.invoke(Command(resume={"approved": True, "note": "photos ok"}), thread)

    assert db.get_order("ORD1003", 2)["refund_status"] == "Refunded"
    reply = state["messages"][-1].content
    assert "Refund of Rs 12999 issued" in reply
    assert "photos ok" in reply
    assert "__interrupt__" not in state


def test_resume_with_denial_leaves_the_database_untouched(thread):
    send(thread, "rohit.v@example.com")
    send(thread, "please refund ORD1003")

    state = graph.invoke(Command(resume={"approved": False, "note": "no proof"}), thread)

    assert db.get_order("ORD1003", 2)["refund_status"] is None
    reply = state["messages"][-1].content
    assert "not approved" in reply
    assert "no proof" in reply


def test_approval_issues_exactly_one_refund(thread):
    """
    The node restarts on resume, so everything above interrupt() runs twice.
    This is the test that fails if the write ever drifts above the interrupt:
    refunded_at would be rewritten, or a non-idempotent write would double up.
    """
    send(thread, "rohit.v@example.com")
    send(thread, "please refund ORD1003")
    graph.invoke(Command(resume=True), thread)

    order = db.get_order("ORD1003", 2)
    first_stamp = order["refunded_at"]
    assert order["refund_status"] == "Refunded"
    # A second approval on a settled order must change nothing.
    assert db.refund_order("ORD1003", 2) == 0
    assert db.get_order("ORD1003", 2)["refunded_at"] == first_stamp


def test_damaged_item_claim_escalates_even_when_cheap(thread):
    """
    ORD1006 is Rs 2,499, comfortably under the threshold, so value alone would
    auto-approve it. A damage claim carries fraud risk and goes to a human
    anyway.
    """
    send(thread, "ananya.s@example.com")
    state = send(thread, "ORD1006 arrived damaged, please refund it")

    assert "__interrupt__" in state
    assert "damaged" in state["__interrupt__"][0].value["reason"]
    assert db.get_order("ORD1006", 1)["refund_status"] is None


def test_ordinary_cheap_refund_does_not_pause(thread):
    send(thread, "ananya.s@example.com")
    state = send(thread, "please refund ORD1006")

    assert "__interrupt__" not in state
    assert db.get_order("ORD1006", 1)["refund_status"] == "Refunded"


def test_unrecognised_decision_is_treated_as_denial():
    """Anything that isn't a clear approval must not move money."""
    from agents import normalize_decision

    assert normalize_decision({"approved": True}) == (True, "")
    assert normalize_decision(True) == (True, "")
    assert normalize_decision("approve")[0] is True
    assert normalize_decision("deny")[0] is False
    assert normalize_decision(None)[0] is False
    assert normalize_decision("")[0] is False
    assert normalize_decision({"note": "looks fine"})[0] is False


def test_approval_arguments_are_not_model_fillable():
    """An LLM must not be able to approve its own refund."""
    assert "approved" not in process_refund.args
    assert "customer_id" not in process_refund.args
