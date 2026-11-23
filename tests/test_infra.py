"""
Infrastructure: the LLM response cache, and the dual-mode data layer.

The Redis and Postgres tests SKIP unless REDIS_URL / DATABASE_URL are set, so
the default `pytest tests/ -q` stays green on a clone with no services running.
Set them to exercise the real thing:

    $env:REDIS_URL    = "redis://127.0.0.1:6379"
    $env:DATABASE_URL = "postgresql://user:pass@localhost/support"
"""

import os

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration

from agent_orchestration import cache, db

REDIS_URL = os.environ.get("REDIS_URL")
# Captured at import, BEFORE conftest's autouse fixture pins the backend to
# SQLite for each test. Without this the postgres tests would quietly run
# against SQLite and still pass, which is worse than skipping.
POSTGRES_CONFIGURED = db.BACKEND == "postgres"
REAL_DATABASE_URL = db.DATABASE_URL

needs_redis = pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL not set")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_CONFIGURED, reason="DATABASE_URL not set"
)


def _reset_refunds():
    """
    Clear refund state so these tests are repeatable.

    A transaction rollback would be tidier, but db.py opens and commits its own
    connections - which is the right design for the app and simply means the
    test has to clean up after itself explicitly.
    """
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET refund_status = NULL, refunded_at = NULL")
    conn.commit()
    conn.close()


@pytest.fixture
def postgres(monkeypatch):
    """Undo conftest's SQLite pin, so these tests really hit Postgres."""
    monkeypatch.setattr(db, "BACKEND", "postgres")
    monkeypatch.setattr(db, "DATABASE_URL", REAL_DATABASE_URL)
    db.init_db()
    _reset_refunds()
    yield
    _reset_refunds()


# ---------------------------------------------------------------- cache


def test_cache_falls_back_to_in_process_without_redis(monkeypatch):
    monkeypatch.setattr(cache, "REDIS_URL", None)
    assert cache.install() == "in-process"
    assert cache.stats()["backend"] == "in-process"


def test_unreachable_redis_degrades_rather_than_failing(monkeypatch, capsys):
    """A cache is an optimisation. Losing it must never take the system down."""
    monkeypatch.setattr(cache, "REDIS_URL", "redis://127.0.0.1:1")  # nothing there
    assert cache.install() == "in-process"
    assert "Redis unreachable" in capsys.readouterr().out


@pytest.fixture
def redis_cache():
    store = cache.RedisResponseCache(REDIS_URL, ttl=60, prefix="pytest:llm")
    store.clear()
    yield store
    store.clear()


@needs_redis
def test_round_trip_and_ttl(redis_cache):
    redis_cache.update(
        "p", "cfg", [ChatGeneration(message=AIMessage(content="order_lookup"))]
    )
    generations = redis_cache.lookup("p", "cfg")
    assert generations[0].message.content == "order_lookup"
    assert 0 < redis_cache.client.ttl(redis_cache._key("p", "cfg")) <= 60


@needs_redis
def test_tool_calls_survive_the_cache(redis_cache):
    """
    The bug this guards is nasty: a cache that dropped tool_calls would break
    the tool-calling loop only on the SECOND time a prompt was seen.
    """
    calls = [
        {
            "name": "check_order_status",
            "args": {"order_id": "ORD1002"},
            "id": "c1",
            "type": "tool_call",
        }
    ]
    redis_cache.update(
        "p", "cfg", [ChatGeneration(message=AIMessage(content="", tool_calls=calls))]
    )
    assert redis_cache.lookup("p", "cfg")[0].message.tool_calls == calls


@needs_redis
def test_a_miss_is_a_miss(redis_cache):
    assert redis_cache.lookup("never-seen", "cfg") is None


@needs_redis
def test_different_model_config_is_a_different_key(redis_cache):
    """Same prompt, different model, must not serve the other model's answer."""
    redis_cache.update("p", "modelA", [ChatGeneration(message=AIMessage(content="A"))])
    assert redis_cache.lookup("p", "modelB") is None


@needs_redis
def test_stats_count_entries_not_counters(redis_cache):
    """
    Regression: entries and hit counters shared one prefix, so `entries`
    included the stats keys and reported 2 for a single cached response.
    """
    redis_cache.update("p", "cfg", [ChatGeneration(message=AIMessage(content="x"))])
    redis_cache.lookup("p", "cfg")  # 1 hit
    redis_cache.lookup("q", "cfg")  # 1 miss
    stats = redis_cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


@needs_redis
def test_cache_is_shared_across_clients(redis_cache):
    """Two API processes must see one cache, or the point is lost."""
    redis_cache.update(
        "shared", "cfg", [ChatGeneration(message=AIMessage(content="hi"))]
    )
    other = cache.RedisResponseCache(REDIS_URL, ttl=60, prefix="pytest:llm")
    assert other.lookup("shared", "cfg")[0].message.content == "hi"


# ---------------------------------------------------------------- data layer


def test_placeholder_translation_is_backend_specific(monkeypatch):
    sql = "SELECT * FROM orders WHERE order_id = ? AND customer_id = ?"
    assert db._sql(sql) == sql  # sqlite: unchanged
    monkeypatch.setattr(db, "BACKEND", "postgres")
    assert db._sql(sql).count("%s") == 2
    assert "?" not in db._sql(sql)


def test_describe_backend_never_leaks_the_connection_string(monkeypatch):
    assert db.describe_backend().startswith("sqlite:")
    monkeypatch.setattr(db, "BACKEND", "postgres")
    described = db.describe_backend()
    assert described == "postgres"
    assert "@" not in described and "//" not in described


@needs_postgres
def test_security_invariants_hold_on_postgres(postgres):
    """
    The whole point of dual-mode: the same guarantees, both backends. These
    mirror the SQLite assertions in test_security.py exactly.
    """
    assert db.get_order("ORD1003", 2) is not None  # real owner
    assert db.get_order("ORD1003", 1) is None  # someone else
    assert {o["order_id"] for o in db.list_orders(1)} == {
        "ORD1001",
        "ORD1002",
        "ORD1006",
    }


@needs_postgres
def test_refund_scoping_and_idempotency_hold_on_postgres(postgres):
    assert db.refund_order("ORD1006", 2) == 0  # not customer 2's order
    assert db.refund_order("ORD1002", 1) == 0  # not delivered
    assert db.refund_order("ORD1006", 1) == 1  # the real thing
    assert db.refund_order("ORD1006", 1) == 0  # idempotent
