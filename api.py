"""
HTTP API over the graph — the backend for the Phase 7 frontend.

Two things the frontend needs that the CLI never did:

  1. A conversation is addressed by thread_id and survives between requests.
     That is the checkpointer's job, already in place since Phase 2.
  2. Pending refund approvals must be visible ACROSS conversations, because a
     reviewer works a queue rather than sitting inside one chat. That is what
     list_thread_ids + pending_approval give us.

Uses SqliteSaver rather than InMemorySaver: a web app that forgets every
pending approval on restart is not a human-in-the-loop system.
"""

import os
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

import db
from agents import TEST_MODE
from graph import build_sqlite_graph, list_thread_ids, pending_approval

CHECKPOINT_PATH = os.environ.get("CHECKPOINT_PATH", "checkpoints.sqlite")

# One sqlite connection shared by sync endpoints running in a thread pool.
# Serialising graph work is the simplest correct answer at this scale, and it
# also means two reviewers cannot resume the same interrupt concurrently.
GRAPH_LOCK = threading.Lock()

app = FastAPI(title="Order Support Agent API", version="7.0")

# The dev frontend runs on a different port, so the browser needs this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

graph, checkpointer = build_sqlite_graph(CHECKPOINT_PATH)
db.init_db()


# ----------------------------- payloads -----------------------------

class MessageIn(BaseModel):
    text: str = Field(min_length=1)


class DecisionIn(BaseModel):
    approved: bool
    note: str = ""


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
def health():
    return {"ok": True, "mode": "TEST_MODE" if TEST_MODE else "LIVE_MODE"}


@app.post("/api/threads", status_code=201)
def create_thread():
    return {"thread_id": f"web-{uuid.uuid4().hex[:12]}"}


@app.get("/api/threads")
def list_threads():
    with GRAPH_LOCK:
        views = [thread_view(t) for t in list_thread_ids(checkpointer)]
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
def get_thread(thread_id: str):
    with GRAPH_LOCK:
        view = thread_view(thread_id)
    if not view["messages"]:
        raise HTTPException(status_code=404, detail="No such conversation")
    return view


@app.post("/api/threads/{thread_id}/messages")
def send_message(thread_id: str, body: MessageIn):
    config = {"configurable": {"thread_id": thread_id}}
    with GRAPH_LOCK:
        before = len(graph.get_state(config).values.get("messages", []))
        graph.invoke({"messages": [HumanMessage(content=body.text)]}, config)
        view = thread_view(thread_id)
    # +1 skips the customer's own message, which the UI already displayed.
    view["new_messages"] = view["messages"][before + 1:]
    return view


@app.get("/api/escalations")
def escalations():
    """
    Every conversation currently waiting on a human. This is the queue.

    Only the checkpointer knows which threads exist, which is why
    build_sqlite_graph hands it back rather than keeping it private.
    """
    queue = []
    with GRAPH_LOCK:
        for thread_id in list_thread_ids(checkpointer):
            waiting = pending_approval(graph, {"configurable": {"thread_id": thread_id}})
            if waiting:
                queue.append({"thread_id": thread_id, **waiting})
    return queue


@app.post("/api/escalations/{thread_id}/decide")
def decide(thread_id: str, body: DecisionIn):
    config = {"configurable": {"thread_id": thread_id}}
    with GRAPH_LOCK:
        if pending_approval(graph, config) is None:
            # Already decided, or never paused. Refusing is important: resuming
            # a thread that is not waiting would drive it from wherever it is.
            raise HTTPException(status_code=409, detail="Not awaiting approval")
        before = len(graph.get_state(config).values.get("messages", []))
        graph.invoke(Command(resume=body.model_dump()), config)
        view = thread_view(thread_id)
    view["new_messages"] = view["messages"][before:]   # no echo to skip on resume
    return view
