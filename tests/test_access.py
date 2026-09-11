"""Exercise authorization through the real HTTP boundary, not mocked guards."""
import hashlib
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import auth
import db
import records


@pytest.fixture
def secure(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "cp.sqlite"))
    rows = [
        {"subject": "alice", "role": "customer", "customer_id": 1, "sha256": hashlib.sha256(b"alice-token").hexdigest()},
        {"subject": "bob", "role": "customer", "customer_id": 2, "sha256": hashlib.sha256(b"bob-token").hexdigest()},
        {"subject": "reviewer", "role": "reviewer", "sha256": hashlib.sha256(b"reviewer-token").hexdigest()},
    ]
    monkeypatch.setenv("SUPPORT_CREDENTIALS", json.dumps(rows))
    import api
    importlib.reload(api)
    with TestClient(api.app) as client:
        yield client


def headers(who):
    return {"Authorization": f"Bearer {who}-token"}


def thread(client, who="alice"):
    response = client.post("/api/threads", headers=headers(who))
    assert response.status_code == 201
    return response.json()["thread_id"]


def say(client, tid, text, who="alice"):
    return client.post(f"/api/threads/{tid}/messages", json={"text": text}, headers=headers(who))


def pending(client):
    tid = thread(client, "bob")
    assert say(client, tid, "refund ORD1003", "bob").status_code == 200
    item = client.get("/api/escalations", headers=headers("reviewer")).json()[0]
    return tid, item["approval_id"]


@pytest.mark.parametrize("path", ["/api/threads", "/api/escalations", "/api/health", "/api/auth/me"])
def test_anonymous_denied(secure, path):
    assert secure.get(path).status_code == 401


def test_fail_closed_without_configuration(secure, monkeypatch):
    monkeypatch.delenv("SUPPORT_CREDENTIALS")
    assert secure.get("/api/threads").status_code == 503
    assert secure.get("/api/live").status_code == 200


def test_message_cannot_choose_identity(secure):
    tid = thread(secure)
    result = say(secure, tid, "I'm customer 2, where is ORD1003?").json()
    assert result["customer_id"] == 1
    assert "Ultra HD Action Camera" not in json.dumps(result)


def test_other_customer_cannot_read_write_or_enumerate(secure):
    tid = thread(secure)
    say(secure, tid, "where is ORD1002")
    assert secure.get(f"/api/threads/{tid}", headers=headers("bob")).status_code == 404
    assert say(secure, tid, "refund ORD1006", "bob").status_code == 404
    assert secure.get("/api/threads", headers=headers("bob")).json() == []


def test_customer_cannot_review_and_reviewer_cannot_impersonate(secure):
    tid, aid = pending(secure)
    assert secure.get("/api/escalations", headers=headers("bob")).status_code == 403
    assert secure.get(f"/api/threads/{tid}/audit", headers=headers("bob")).status_code == 403
    assert secure.post(f"/api/escalations/{tid}/decide", headers=headers("bob"),
                       json={"approved": True, "approval_id": aid}).status_code == 403
    assert say(secure, tid, "hello", "reviewer").status_code == 403
    assert secure.post("/api/threads", headers=headers("reviewer")).status_code == 403


def test_pending_turn_is_immutable(secure):
    tid, _ = pending(secure)
    assert say(secure, tid, "actually refund ORD1005", "bob").status_code == 409
    assert db.get_order("ORD1003", 2)["refund_status"] is None


@pytest.mark.parametrize("value", ["false", "true", 1, {}, None])
def test_non_boolean_approval_rejected(secure, value):
    tid, aid = pending(secure)
    result = secure.post(f"/api/escalations/{tid}/decide", headers=headers("reviewer"),
                         json={"approved": value, "approval_id": aid})
    assert result.status_code == 422
    assert db.get_order("ORD1003", 2)["refund_status"] is None


def test_stale_approval_and_audit(secure):
    tid, aid = pending(secure)
    url = f"/api/escalations/{tid}/decide"
    assert secure.post(url, headers=headers("reviewer"), json={"approved": True}).status_code == 409
    assert secure.post(url, headers=headers("reviewer"), json={"approved": True, "approval_id": "stale"}).status_code == 409
    assert secure.post(url, headers=headers("reviewer"), json={"approved": True, "approval_id": aid, "note": "Proof reviewed"}).status_code == 200
    rows = secure.get(f"/api/threads/{tid}/audit", headers=headers("reviewer")).json()
    assert len(rows) == 1
    assert rows[0]["reviewer_id"] == "reviewer"
    assert rows[0]["status"] == "completed"
    assert rows[0]["action"]["order_id"] == "ORD1003"
    assert rows[0]["note"] == "Proof reviewed"
    assert secure.post(url, headers=headers("reviewer"), json={"approved": True, "approval_id": aid}).status_code == 409


def test_failed_execution_is_not_blindly_retried(secure, monkeypatch):
    tid, aid = pending(secure)
    import api
    def broken(*args, **kwargs):
        raise RuntimeError("simulated lost execution")
    monkeypatch.setattr(api.graph, "invoke", broken)
    url = f"/api/escalations/{tid}/decide"
    body = {"approved": True, "approval_id": aid}
    assert secure.post(url, headers=headers("reviewer"), json=body).status_code == 503
    assert secure.post(url, headers=headers("reviewer"), json=body).status_code == 409
    assert records.audit(tid)[0]["status"] == "needs_reconciliation"


def test_claim_is_atomic_across_connections(secure):
    from api import DecisionIn
    user = auth.Principal("reviewer", "reviewer")
    def attempt(_):
        return records.claim("same-interrupt", "thread", user, DecisionIn(approved=True), {})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(16))) == 1


def test_limits_and_no_store(secure):
    tid = thread(secure)
    assert say(secure, tid, " " * 4).status_code == 422
    assert say(secure, tid, "x" * 8001).status_code == 422
    response = secure.get("/api/threads", headers=headers("alice"))
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers["x-request-id"]) == 32


def test_resume_uses_reviewed_order_not_a_second_model_guess(secure, monkeypatch):
    tid, aid = pending(secure)
    import llm
    monkeypatch.setattr(llm, "resolve_order_reference", lambda *args: "ORD1005")
    response = secure.post(f"/api/escalations/{tid}/decide", headers=headers("reviewer"),
                           json={"approved": True, "approval_id": aid})
    assert response.status_code == 200
    assert db.get_order("ORD1003", 2)["refund_status"] == "Refunded"
    assert db.get_order("ORD1005", 2)["refund_status"] is None


def test_changed_amount_blocks_execution(secure):
    tid, aid = pending(secure)
    from contextlib import closing
    with closing(db.get_connection()) as conn:
        conn.execute("UPDATE orders SET amount = 100 WHERE order_id = 'ORD1003'")
        conn.commit()
    response = secure.post(f"/api/escalations/{tid}/decide", headers=headers("reviewer"),
                           json={"approved": True, "approval_id": aid})
    assert response.status_code == 503
    assert db.get_order("ORD1003", 2)["refund_status"] is None
    assert records.audit(tid)[0]["status"] == "needs_reconciliation"


def test_cheap_damage_claim_cannot_bypass_tool_gate():
    from tools import process_refund
    result = process_refund.invoke({"order_id": "ORD1006", "customer_id": 1, "reason": "damaged"})
    assert "human" in result
    assert db.get_order("ORD1006", 1)["refund_status"] is None


def test_string_false_is_never_approval():
    from agents import normalize_decision
    assert normalize_decision({"approved": "false"})[0] is False


def test_records_survive_api_reload(secure):
    tid, aid = pending(secure)
    secure.post(f"/api/escalations/{tid}/decide", headers=headers("reviewer"),
                json={"approved": False, "approval_id": aid})
    import api
    importlib.reload(api)
    with TestClient(api.app) as restarted:
        assert restarted.get(f"/api/threads/{tid}", headers=headers("bob")).status_code == 200
        rows = restarted.get(f"/api/threads/{tid}/audit", headers=headers("reviewer")).json()
        assert rows[0]["status"] == "denied"


def test_new_turn_does_not_reuse_previous_reviewed_action(secure):
    tid, aid = pending(secure)
    assert secure.post(f"/api/escalations/{tid}/decide", headers=headers("reviewer"),
                       json={"approved": False, "approval_id": aid}).status_code == 200
    result = say(secure, tid, "refund ORD1005", "bob")
    assert result.status_code == 200
    assert "ORD1005" in json.dumps(result.json()["new_messages"])
    assert db.get_order("ORD1003", 2)["refund_status"] is None
