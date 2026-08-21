"""
API tests for the Phase 7 backend.

Skipped entirely when the API extras are absent, so
`pip install -r requirements.txt && pytest tests/ -q` stays green with only the
core dependencies installed.
"""
import pytest

pytest.importorskip("fastapi", reason="API extras not installed (requirements-api.txt)")

from fastapi.testclient import TestClient   # noqa: E402

import db                                    # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app with its own checkpoint file and its own seeded database."""
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "cp.sqlite"))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "api_support.db")

    import api
    import importlib
    importlib.reload(api)                    # rebind the graph to this temp state
    with TestClient(api.app) as test_client:
        yield test_client


def new_thread(client):
    return client.post("/api/threads").json()["thread_id"]


def say(client, thread_id, text):
    response = client.post(f"/api/threads/{thread_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


# --- basics ---

def test_health_reports_mode(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mode"] in ("TEST_MODE", "LIVE_MODE")


def test_unknown_thread_is_404(client):
    assert client.get("/api/threads/does-not-exist").status_code == 404


def test_conversation_survives_across_requests(client):
    """The whole point of thread_id: request 2 knows who request 1 verified."""
    thread = new_thread(client)
    say(client, thread, "ananya.s@example.com")
    view = say(client, thread, "where is ORD1002")

    assert view["verified"] is True
    assert view["customer_id"] == 1
    assert any("ORD1002" in m["content"] for m in view["new_messages"])


def test_new_messages_excludes_the_customers_own_message(client):
    thread = new_thread(client)
    view = say(client, thread, "ananya.s@example.com")
    assert all(m["role"] != "user" for m in view["new_messages"])


def test_authors_are_exposed_for_the_activity_feed(client):
    thread = new_thread(client)
    say(client, thread, "ananya.s@example.com")
    view = say(client, thread, "where is ORD1002")
    authors = {m["author"] for m in view["messages"]}
    assert "customer" in authors and "order_agent" in authors


# --- the escalation queue ---

def test_queue_is_empty_until_something_escalates(client):
    thread = new_thread(client)
    say(client, thread, "ananya.s@example.com")
    say(client, thread, "where is ORD1002")
    assert client.get("/api/escalations").json() == []


def test_high_value_refund_appears_in_the_queue(client):
    thread = new_thread(client)
    say(client, thread, "rohit.v@example.com")
    view = say(client, thread, "please refund ORD1003")

    assert view["awaiting_approval"]["order_id"] == "ORD1003"

    queue = client.get("/api/escalations").json()
    assert len(queue) == 1
    assert queue[0]["thread_id"] == thread
    assert queue[0]["amount"] == 12999.0
    assert "auto-approval limit" in queue[0]["reason"]
    # risk_summary is LLM-written with a key and mirrors reason without one,
    # but the field must always be present for the UI to rely on.
    assert queue[0]["risk_summary"]


def test_queue_spans_multiple_conversations(client):
    """A reviewer works a queue, not one chat. This is why it reads all threads."""
    first = new_thread(client)
    say(client, first, "rohit.v@example.com")
    say(client, first, "please refund ORD1003")

    second = new_thread(client)
    say(client, second, "meera.n@example.com")
    say(client, second, "ORD1007 arrived damaged, refund it")

    queue = client.get("/api/escalations").json()
    assert {q["thread_id"] for q in queue} == {first, second}
    assert {q["order_id"] for q in queue} == {"ORD1003", "ORD1007"}


def test_approving_from_the_queue_issues_the_refund(client):
    thread = new_thread(client)
    say(client, thread, "rohit.v@example.com")
    say(client, thread, "please refund ORD1003")

    response = client.post(
        f"/api/escalations/{thread}/decide", json={"approved": True, "note": "photos ok"}
    )
    assert response.status_code == 200
    view = response.json()

    assert view["awaiting_approval"] is None
    assert any("Refund of Rs 12999 issued" in m["content"] for m in view["new_messages"])
    assert client.get("/api/escalations").json() == []
    assert db.get_order("ORD1003", 2)["refund_status"] == "Refunded"


def test_denying_from_the_queue_writes_nothing(client):
    thread = new_thread(client)
    say(client, thread, "rohit.v@example.com")
    say(client, thread, "please refund ORD1003")

    view = client.post(
        f"/api/escalations/{thread}/decide", json={"approved": False, "note": "no proof"}
    ).json()

    assert any("not approved" in m["content"] for m in view["new_messages"])
    assert db.get_order("ORD1003", 2)["refund_status"] is None
    assert client.get("/api/escalations").json() == []


def test_deciding_twice_is_a_conflict_not_a_second_refund(client):
    """
    Two reviewers opening the same queue item is the obvious race. Resuming a
    thread that is not waiting would drive it onward from wherever it sits, so
    the second decision must be refused rather than applied.
    """
    thread = new_thread(client)
    say(client, thread, "rohit.v@example.com")
    say(client, thread, "please refund ORD1003")

    assert client.post(f"/api/escalations/{thread}/decide", json={"approved": True}).status_code == 200
    again = client.post(f"/api/escalations/{thread}/decide", json={"approved": True})
    assert again.status_code == 409


def test_deciding_on_a_thread_that_never_paused_is_a_conflict(client):
    thread = new_thread(client)
    say(client, thread, "ananya.s@example.com")
    assert client.post(f"/api/escalations/{thread}/decide", json={"approved": True}).status_code == 409


def test_thread_list_flags_the_ones_needing_attention(client):
    quiet = new_thread(client)
    say(client, quiet, "ananya.s@example.com")
    say(client, quiet, "where is ORD1002")

    waiting = new_thread(client)
    say(client, waiting, "rohit.v@example.com")
    say(client, waiting, "please refund ORD1003")

    rows = {r["thread_id"]: r for r in client.get("/api/threads").json()}
    assert rows[quiet]["awaiting_approval"] is False
    assert rows[waiting]["awaiting_approval"] is True
    assert rows[waiting]["customer_name"] == "Rohit Verma"


def test_pause_survives_a_server_restart(client, tmp_path, monkeypatch):
    """
    The reason this uses SqliteSaver rather than InMemorySaver. A reviewer may
    not get to the queue before the process is redeployed.
    """
    thread = new_thread(client)
    say(client, thread, "rohit.v@example.com")
    say(client, thread, "please refund ORD1003")

    import api
    import importlib
    importlib.reload(api)                     # same CHECKPOINT_PATH, new process-ish
    with TestClient(api.app) as restarted:
        queue = restarted.get("/api/escalations").json()
        assert [q["order_id"] for q in queue] == ["ORD1003"]
