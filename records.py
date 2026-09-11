"""Application ownership and durable decisions, separate from graph checkpoints.

Decision claims use a unique interrupt ID. A crash leaves an executing record
for manual reconciliation; an ambiguous money operation is never blindly retried.
"""

import json
from contextlib import closing
from datetime import datetime, timezone

import db


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize():
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS conversations (
            thread_id TEXT PRIMARY KEY, subject TEXT NOT NULL,
            customer_id INTEGER, created_at TEXT NOT NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS conversations_subject ON conversations(subject, created_at)")
        cur.execute("""CREATE TABLE IF NOT EXISTS approval_decisions (
            approval_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            reviewer_id TEXT NOT NULL, approved INTEGER NOT NULL,
            note TEXT NOT NULL, action_json TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT)""")
        cur.execute("CREATE INDEX IF NOT EXISTS decisions_thread ON approval_decisions(thread_id, created_at)")
        conn.commit()


def create(thread_id, user):
    with closing(db.get_connection()) as conn:
        conn.cursor().execute(db._sql("INSERT INTO conversations VALUES (?, ?, ?, ?)"),
                              (thread_id, user.subject, user.customer_id, now()))
        conn.commit()


def conversation(thread_id):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(db._sql("SELECT * FROM conversations WHERE thread_id = ?"), (thread_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def threads(user, limit=100, offset=0):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        where = " WHERE subject = ? AND customer_id = ?" if user.role == "customer" else ""
        args = (user.subject, user.customer_id) if where else ()
        cur.execute(db._sql("SELECT thread_id FROM conversations" + where +
                            " ORDER BY created_at DESC LIMIT ? OFFSET ?"), (*args, limit, offset))
        return [row["thread_id"] for row in cur.fetchall()]


def claim(approval_id, thread_id, user, body, action):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(db._sql("""INSERT INTO approval_decisions
            (approval_id, thread_id, reviewer_id, approved, note, action_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'executing', ?) ON CONFLICT (approval_id) DO NOTHING"""),
            (approval_id, thread_id, user.subject, int(body.approved), body.note,
             json.dumps(action, sort_keys=True), now()))
        won = cur.rowcount == 1
        conn.commit()
        return won


def finish(approval_id, status):
    with closing(db.get_connection()) as conn:
        conn.cursor().execute(db._sql("""UPDATE approval_decisions SET status = ?, completed_at = ?
            WHERE approval_id = ? AND status = 'executing'"""), (status, now(), approval_id))
        conn.commit()


def audit(thread_id):
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(db._sql("SELECT * FROM approval_decisions WHERE thread_id = ? ORDER BY created_at DESC"), (thread_id,))
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["action"] = json.loads(row.pop("action_json"))
        row["approved"] = bool(row["approved"])
    return rows
