"""
Shared fixtures.

The important one is `temp_db`: Phase 4 introduced the first tool that WRITES,
so the suite can no longer run against the developer's real support.db without
mutating it. Every test gets a freshly seeded database in a temp directory.
"""
import pytest
import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every query at a fresh, seeded database for the duration of a test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_support.db")
    db.init_db()
    yield
