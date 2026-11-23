"""FastAPI routes for conversations and refund review."""

import os
from _thread import LockType
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from typing import Annotated, NamedTuple
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from pydantic import BaseModel, Field

from agent_orchestration import cache, db, llm
from agent_orchestration.graph import (
    build_graph,
    list_thread_ids,
    open_checkpointer,
    pending_approval,
)

router = APIRouter(prefix="/api")


class MessageIn(BaseModel):
    text: str = Field(min_length=1)


class DecisionIn(BaseModel):
    approved: bool
    note: str = ""


class GraphRuntime(NamedTuple):
    graph: CompiledStateGraph
    checkpointer: BaseCheckpointSaver
    lock: LockType


def get_runtime(request: Request) -> GraphRuntime:
    return request.state.runtime


GraphState = Annotated[GraphRuntime, Depends(get_runtime)]


def thread_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id}}


def serialize(message):
    if isinstance(message, HumanMessage):
        role, author = "user", "customer"
    elif isinstance(message, AIMessage):
        role, author = "agent", message.name or "agent"
    else:
        role, author = "system", message.name or "system"
    return {"role": role, "author": author, "content": message.content}


def thread_view(thread_id: str, snapshot: StateSnapshot):
    values = snapshot.values
    return {
        "thread_id": thread_id,
        "verified": bool(values.get("verified")),
        "customer_id": values.get("customer_id"),
        "customer_name": values.get("customer_name"),
        "messages": [serialize(message) for message in values.get("messages", [])],
        "awaiting_approval": pending_approval(snapshot),
    }


@router.get("/health")
def health():
    return {
        "ok": True,
        "mode": "LIVE_MODE" if llm.live() else "TEST_MODE",
        "model": llm.describe(),
        "database": db.describe_backend(),
        "cache": cache.stats(),
    }


@router.post("/threads", status_code=201)
def create_thread():
    return {"thread_id": f"web-{uuid4().hex[:12]}"}


@router.get("/threads")
def list_threads(runtime: GraphState):
    with runtime.lock:
        threads = []
        for thread_id in list_thread_ids(runtime.checkpointer):
            snapshot = runtime.graph.get_state(thread_config(thread_id))
            values = snapshot.values
            messages = values.get("messages", [])
            threads.append(
                {
                    "thread_id": thread_id,
                    "customer_name": values.get("customer_name"),
                    "verified": bool(values.get("verified")),
                    "message_count": len(messages),
                    "awaiting_approval": pending_approval(snapshot) is not None,
                    "last_message": messages[-1].content if messages else None,
                }
            )
    return threads


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, runtime: GraphState):
    with runtime.lock:
        snapshot = runtime.graph.get_state(thread_config(thread_id))
    if not snapshot.values.get("messages"):
        raise HTTPException(status_code=404, detail="No such conversation")
    return thread_view(thread_id, snapshot)


@router.post("/threads/{thread_id}/messages")
def send_message(thread_id: str, body: MessageIn, runtime: GraphState):
    config = thread_config(thread_id)
    with runtime.lock:
        before = len(runtime.graph.get_state(config).values.get("messages", []))
        runtime.graph.invoke({"messages": [HumanMessage(content=body.text)]}, config)
        view = thread_view(thread_id, runtime.graph.get_state(config))
    # The client already has the customer message.
    view["new_messages"] = view["messages"][before + 1 :]
    return view


@router.get("/escalations")
def escalations(runtime: GraphState):
    with runtime.lock:
        queue = []
        for thread_id in list_thread_ids(runtime.checkpointer):
            snapshot = runtime.graph.get_state(thread_config(thread_id))
            waiting = pending_approval(snapshot)
            if waiting:
                queue.append({"thread_id": thread_id, **waiting})
    return queue


@router.post("/escalations/{thread_id}/decide")
def decide(thread_id: str, body: DecisionIn, runtime: GraphState):
    config = thread_config(thread_id)
    with runtime.lock:
        snapshot = runtime.graph.get_state(config)
        if pending_approval(snapshot) is None:
            raise HTTPException(status_code=409, detail="Not awaiting approval")
        before = len(snapshot.values.get("messages", []))
        runtime.graph.invoke(Command(resume=body.model_dump()), config)
        view = thread_view(thread_id, runtime.graph.get_state(config))
    view["new_messages"] = view["messages"][before:]
    return view


def create_app(checkpoint_path: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        path = Path(
            checkpoint_path or os.getenv("CHECKPOINT_PATH", "checkpoints.sqlite")
        )
        db.init_db()
        with open_checkpointer(path) as checkpointer:
            # Serialize graph operations so two reviewers cannot resume one turn.
            yield {
                "runtime": GraphRuntime(build_graph(checkpointer), checkpointer, Lock())
            }

    app = FastAPI(
        title="Order Support Agent API",
        version=version("agent-orchestration"),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
