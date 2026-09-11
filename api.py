"""FastAPI boundary for conversations and refund review."""

import os
import threading
import uuid
import logging
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field, StrictBool

import cache
import db
import llm
import auth
import records
from graph import build_durable_graph, pending_approval

CHECKPOINT_PATH = os.environ.get("CHECKPOINT_PATH", "checkpoints.sqlite")

# The SQLite checkpointer shares one connection across request threads.
# Deployment is intentionally limited to one worker; see the operations guide.
GRAPH_LOCK = threading.Lock()

app = FastAPI(title="Order Support Agent API", version="9.0")

# The dev frontend runs on a different port, so the browser needs this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

graph, checkpointer = build_durable_graph(CHECKPOINT_PATH)
db.init_db()
records.initialize()


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    logging.getLogger("support.http").info(
        "request_id=%s method=%s status=%s duration_ms=%.1f",
        request_id, request.method, response.status_code, (time.monotonic() - started) * 1000,
    )
    return response


def authorize_thread(thread_id, user):
    row = records.conversation(thread_id)
    if row is None or (user.role == "customer" and
                       (row["subject"] != user.subject or row["customer_id"] != user.customer_id)):
        raise HTTPException(404, "No such conversation")
    return row


# ----------------------------- payloads -----------------------------

class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000, pattern=r"\S")


class DecisionIn(BaseModel):
    approved: StrictBool
    note: str = Field(default="", max_length=2000)
    approval_id: str | None = None


def serialize(message):
    """One message, in the shape the activity feed renders."""
    if isinstance(message, HumanMessage):
        role, author = "user", "customer"
    elif isinstance(message, AIMessage):
        role, author = "agent", getattr(message, "name", None) or "agent"
    else:
        role, author = "system", getattr(message, "name", None) or "system"
    return {"role": role, "author": author, "content": message.content}


def thread_view(thread_id):
    """Everything the UI needs about one conversation."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    return {
        "thread_id": thread_id,
        "verified": bool(values.get("verified")),
        "customer_id": values.get("customer_id"),
        "customer_name": values.get("customer_name"),
        "messages": [serialize(m) for m in values.get("messages", [])],
        "awaiting_approval": pending_approval(graph, config),
    }


# ----------------------------- endpoints -----------------------------

@app.get("/api/health")
def health(user: auth.Principal = Depends(auth.principal)):
    return {
        "ok": True,
        "mode": "LIVE_MODE" if llm.live() else "TEST_MODE",
        "model": llm.describe(),
        "database": db.describe_backend(),
        "cache": cache.stats(),
        "auth_mode": "demo" if auth.demo_mode() else "authenticated",
    }


@app.get("/api/auth/config")
def auth_config():
    return {"demo": auth.demo_mode()}


@app.get("/api/auth/me")
def me(user: auth.Principal = Depends(auth.principal)):
    return {"subject": user.subject, "role": user.role, "customer_id": user.customer_id}


@app.get("/api/live")
def liveness():
    return {"ok": True}


@app.post("/api/threads", status_code=201)
def create_thread(user: auth.Principal = Depends(auth.principal)):
    auth.require_role(user, "customer")
    thread_id = f"web-{uuid.uuid4().hex}"
    records.create(thread_id, user)
    return {"thread_id": thread_id}


@app.get("/api/threads")
def list_threads(user: auth.Principal = Depends(auth.principal),
                 limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    with GRAPH_LOCK:
        views = [thread_view(t) for t in records.threads(user, limit, offset)]
    return [
        {
            "thread_id": v["thread_id"],
            "customer_name": v["customer_name"],
            "verified": v["verified"],
            "message_count": len(v["messages"]),
            "awaiting_approval": bool(v["awaiting_approval"]),
            "last_message": v["messages"][-1]["content"] if v["messages"] else None,
        }
        for v in views
    ]


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str, user: auth.Principal = Depends(auth.principal)):
    authorize_thread(thread_id, user)
    with GRAPH_LOCK:
        view = thread_view(thread_id)
    return view


@app.post("/api/threads/{thread_id}/messages")
def send_message(thread_id: str, body: MessageIn, user: auth.Principal = Depends(auth.principal)):
    auth.require_role(user, "customer")
    authorize_thread(thread_id, user)
    config = {"configurable": {"thread_id": thread_id}}
    if user.role == "customer":
        config["configurable"]["authenticated_customer_id"] = user.customer_id
    with GRAPH_LOCK:
        if pending_approval(graph, config):
            raise HTTPException(409, "A reviewer must resolve the pending approval before another message")
        before = len(graph.get_state(config).values.get("messages", []))
        graph.invoke({"messages": [HumanMessage(content=body.text)]}, config)
        view = thread_view(thread_id)
    # +1 skips the customer's own message, which the UI already displayed.
    view["new_messages"] = view["messages"][before + 1:]
    return view


@app.get("/api/escalations")
def escalations(user: auth.Principal = Depends(auth.principal),
                limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    """
    Every conversation currently waiting on a human. This is the queue.

    The application registry supplies a bounded conversation page; the
    checkpointer supplies its current interrupts.
    """
    queue = []
    auth.require_role(user, "reviewer")
    with GRAPH_LOCK:
        for thread_id in records.threads(user, limit, offset):
            waiting = pending_approval(graph, {"configurable": {"thread_id": thread_id}})
            if waiting:
                snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
                approval_id = next(p.id for task in snapshot.tasks for p in task.interrupts)
                decisions = records.audit(thread_id)
                status = next((d["status"] for d in decisions if d["approval_id"] == approval_id), "pending")
                queue.append({"thread_id": thread_id, **waiting,
                              "approval_id": approval_id, "status": status})
    return queue


@app.post("/api/escalations/{thread_id}/decide")
def decide(thread_id: str, body: DecisionIn, user: auth.Principal = Depends(auth.principal)):
    auth.require_role(user, "reviewer")
    authorize_thread(thread_id, user)
    config = {"configurable": {"thread_id": thread_id}}
    with GRAPH_LOCK:
        if pending_approval(graph, config) is None:
            # Already decided, or never paused. Refusing is important: resuming
            # a thread that is not waiting would drive it from wherever it is.
            raise HTTPException(status_code=409, detail="Not awaiting approval")
        before = len(graph.get_state(config).values.get("messages", []))
        snapshot = graph.get_state(config)
        approval_id = next(p.id for task in snapshot.tasks for p in task.interrupts)
        if (body.approval_id is None and user.role != "demo") or (
                body.approval_id is not None and body.approval_id != approval_id):
            raise HTTPException(409, "Approval changed; refresh the queue")
        action = pending_approval(graph, config)
        if not records.claim(approval_id, thread_id, user, body, action):
            raise HTTPException(409, "Decision already claimed; inspect the audit record")
        config["configurable"]["reviewed_action"] = action
        try:
            graph.invoke(Command(resume={**body.model_dump(), "action": action}), config)
        except Exception:
            records.finish(approval_id, "needs_reconciliation")
            logging.getLogger("support.decisions").exception("Approval execution failed: %s", approval_id)
            raise HTTPException(503, "Execution outcome requires operator reconciliation") from None
        records.finish(approval_id, "completed" if body.approved else "denied")
        view = thread_view(thread_id)
    view["new_messages"] = view["messages"][before:]   # no echo to skip on resume
    return view


@app.get("/api/threads/{thread_id}/audit")
def audit(thread_id: str, user: auth.Principal = Depends(auth.principal)):
    auth.require_role(user, "reviewer")
    authorize_thread(thread_id, user)
    return records.audit(thread_id)
