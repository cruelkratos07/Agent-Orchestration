"""SQLite/PostgreSQL access for customers and orders.

Queries use ``?`` placeholders and `_sql` translates them for psycopg. Every
order operation is parameterized and scoped by customer ID.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Set this and the whole data layer moves to Postgres. Unset, it is SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL") or None
BACKEND = "postgres" if DATABASE_URL else "sqlite"

DB_PATH = Path(__file__).parent / "support.db"

# Only a delivered order can be refunded. This mirrors the policy text in
# tools.lookup_return_policy ("within 15 days of delivery"). A Shipped order
# would be a cancel-in-transit, which is a different operation; a Cancelled
# order is already refunded by another path.
REFUNDABLE_STATUSES = ("Delivered",)


def _sql(text: str) -> str:
    """
    Translate ? placeholders to %s for psycopg.

    No query in this module contains a literal question mark.
    """
    return text.replace("?", "%s") if BACKEND == "postgres" else text


def get_connection():
    if BACKEND == "postgres":
        import psycopg
        from psycopg.rows import dict_row

        # dict_row makes psycopg rows behave like sqlite3.Row: dict(row) works
        # either way, so nothing downstream has to know which backend it got.
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts, not tuples
    return conn


def describe_backend() -> str:
    """One line for logs and /api/health."""
    if BACKEND == "sqlite":
        return f"sqlite:{DB_PATH.name}"
    # Never echo the URL - it carries the password.
    return "postgres"


def _existing_order_columns(cur) -> set:
    """PRAGMA is SQLite-only, so ask information_schema on Postgres."""
    if BACKEND == "postgres":
        cur.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = 'orders'"
        )
    else:
        cur.execute("PRAGMA table_info(orders)")
    return {row["name"] for row in cur.fetchall()}


def init_db():
    """Create tables and seed demo data. Safe to run repeatedly."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT UNIQUE NOT NULL,
            phone       TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id     TEXT PRIMARY KEY,
            customer_id  INTEGER NOT NULL,
            item         TEXT NOT NULL,
            amount       REAL NOT NULL,
            status       TEXT NOT NULL,
            carrier      TEXT,
            eta          TEXT,
            ordered_on   TEXT NOT NULL,
            refund_status TEXT,
            refunded_at   TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
    """)

    # Older support.db files predate the refund columns. Adding them here keeps
    # init_db() safe to run against an existing database instead of failing
    # with an opaque "no such column" later.
    existing = _existing_order_columns(cur)
    for column in ("refund_status", "refunded_at"):
        if column not in existing:
            cur.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT")

    # Seed only if empty, so re-running doesn't duplicate rows
    cur.execute("SELECT COUNT(*) AS n FROM customers")
    # Both sqlite3.Row and psycopg's dict_row support string indexing, so an
    # aliased column is the portable way to read a scalar.
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            _sql("INSERT INTO customers (customer_id, name, email, phone) VALUES (?, ?, ?, ?)"),
            [
                (1, "Ananya Sharma", "ananya.s@example.com", "+919876543210"),
                (2, "Rohit Verma",   "rohit.v@example.com",  "+919812345678"),
                (3, "Meera Nair",    "meera.n@example.com",  "+919900112233"),
            ],
        )
        cur.executemany(
            _sql("""INSERT INTO orders
               (order_id, customer_id, item, amount, status, carrier, eta, ordered_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""),
            [
                ("ORD1001", 1, "Wireless ANC Earbuds Pro",   4999.00,  "Shipped",    "Delhivery", "2 days",              "2026-07-18"),
                ("ORD1002", 1, "Mechanical Gaming Keyboard", 7499.00,  "Processing", None,        "4 days",              "2026-07-22"),
                ("ORD1003", 2, "Ultra HD Action Camera 4K", 12999.00,  "Delivered",  "BlueDart",  "Delivered 2026-07-20", "2026-07-15"),
                ("ORD1004", 3, "Smart Fitness Watch V2",     3499.00,  "Shipped",    "Ekart",     "1 day",               "2026-07-21"),
                ("ORD1005", 2, "USB-C Fast Charger 65W",     1999.00,  "Cancelled",  None,        None,                  "2026-07-10"),
                ("ORD1006", 1, "Bluetooth Speaker Mini",     2499.00,  "Delivered",  "Delhivery", "Delivered 2026-07-19", "2026-07-12"),
                ("ORD1007", 3, "Laptop Cooling Pad",         1899.00,  "Delivered",  "Ekart",     "Delivered 2026-07-17", "2026-07-11"),
            ],
        )

    conn.commit()
    conn.close()


def find_customer(identifier: str):
    """
    Look up a customer by ID, email, or phone. Returns a dict or None.

    This is the ONLY way a customer_id enters the system. Nothing else
    is allowed to set it — see verify.py for why that matters.
    """
    conn = get_connection()
    cur = conn.cursor()
    ident = identifier.strip()

    # Try numeric customer ID
    if ident.isdigit():
        cur.execute(_sql("SELECT * FROM customers WHERE customer_id = ?"), (int(ident),))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)

    # Try email (case-insensitive)
    cur.execute(_sql("SELECT * FROM customers WHERE LOWER(email) = LOWER(?)"), (ident,))
    row = cur.fetchone()
    if row:
        conn.close()
        return dict(row)

    # Try phone, normalized to digits only so +91-98765 43210 == +919876543210
    digits = "".join(c for c in ident if c.isdigit())
    if digits:
        cur.execute("SELECT * FROM customers")
        rows = cur.fetchall()
        for r in rows:
            if "".join(c for c in r["phone"] if c.isdigit()) == digits:
                conn.close()
                return dict(r)

    conn.close()
    return None


def get_order(order_id: str, customer_id: int):
    """
    Fetch an order — but ONLY if it belongs to this customer.

    The customer_id in the WHERE clause is the security boundary. Even
    if the agent is tricked into asking for someone else's order ID,
    this returns None rather than another customer's data.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        _sql("SELECT * FROM orders WHERE UPPER(order_id) = UPPER(?) AND customer_id = ?"),
        (order_id.strip(), customer_id),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_orders(customer_id: int):
    """All orders for one customer, newest first."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        _sql("SELECT * FROM orders WHERE customer_id = ? ORDER BY ordered_on DESC"),
        (customer_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def refund_order(order_id: str, customer_id: int) -> int:
    """
    Mark an order refunded. Returns the number of rows changed.

    Everything that makes this safe is in the single UPDATE statement:

      * customer_id is in the WHERE clause of the WRITE itself, not checked by
        a preceding SELECT. Reading to verify ownership and then updating by
        order_id alone would be a time-of-check/time-of-use hole, and is one
        careless refactor away from dropping the check entirely.
      * status and refund_status are in the WHERE clause too, which is what
        makes this idempotent. A second attempt matches no rows and returns 0
        instead of refunding twice. Callers distinguish the two cases by the
        return value, not by re-reading the row.
    """
    conn = get_connection()
    placeholders = ", ".join("?" for _ in REFUNDABLE_STATUSES)
    cur = conn.cursor()
    cur.execute(
        _sql(f"""UPDATE orders
               SET refund_status = 'Refunded',
                   refunded_at   = ?
             WHERE UPPER(order_id) = UPPER(?)
               AND customer_id = ?
               AND status IN ({placeholders})
               AND (refund_status IS NULL OR refund_status != 'Refunded')"""),
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            order_id.strip(),
            customer_id,
            *REFUNDABLE_STATUSES,
        ),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed
