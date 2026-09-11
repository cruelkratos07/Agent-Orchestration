"""
Shared fixtures.

`temp_db` gives every test a freshly seeded database in a temp directory. Two
separate reasons it has to exist:

  1. Refund tests write data, so tests cannot share one database without
     leaking state between them.
  2. The data layer is dual-mode. If DATABASE_URL happens to be
     exported - which it will be, on any machine that runs the Postgres setup -
     an autouse fixture that only redirected DB_PATH would silently point the
     whole suite at the real Postgres and refund real rows. It did exactly that
     before this fixture forced the backend.

So the fixture pins the backend to SQLite as well as redirecting the path.
Postgres is covered deliberately by tests/test_infra.py, against a database
the developer chose to point at.
"""
import pytest

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """A fresh, seeded SQLite database for the duration of one test."""
    # Force SQLite even when DATABASE_URL is set: read at call time by
    # db._sql and db.get_connection, so patching the module attributes is
    # enough and no connection to Postgres is ever opened.
    monkeypatch.setattr(db, "BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_URL", None)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_support.db")
    db.init_db()
    yield
