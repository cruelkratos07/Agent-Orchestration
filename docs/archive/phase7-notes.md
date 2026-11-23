# Phase 7 — Web Console (FastAPI + React)

**Status:** implemented. Backend verified by 14 tests plus an end-to-end run
against a live uvicorn process; frontend typechecks and builds clean.

## Why a real API and not a mocked one

A browser cannot call a Python graph, so something has to sit between them. A
React app against hardcoded fixtures would screenshot identically and prove
nothing — the whole point of this phase is showing the orchestration layer
driving a real UI. So: FastAPI over the compiled graph, and the frontend is a
genuine client.

## What the frontend needed that the CLI never did

| Need | Answer |
|---|---|
| A conversation that survives between HTTP requests | `thread_id` + the checkpointer. Already in place since Phase 2. |
| Pending approvals visible **across** conversations | A reviewer works a queue, not one chat. `list_thread_ids` + `pending_approval`. |
| Knowing which agent said what | Every message is serialised with an `author`, so the feed can label lines by specialist instead of collapsing them into one "assistant" voice. |

The second one is why `build_sqlite_graph` returns the checkpointer alongside
the graph: **only the checkpointer knows which threads exist.** Without it the
queue could only ever show the conversation you happen to be looking at.

## SqliteSaver, not InMemorySaver

The Phase 6 notes flagged this as the production change; Phase 7 is where it
stops being optional. A web server that forgets every pending refund approval
when it redeploys is not a human-in-the-loop system — a reviewer may not reach
the queue before a deploy. `test_pause_survives_a_server_restart` reloads the
app against the same checkpoint file and asserts the queue still has the item.

Two consequences worth knowing:

- `check_same_thread=False`, because FastAPI runs sync endpoints in a thread
  pool. Access is serialised with `api.GRAPH_LOCK`.
- The CLI demo and the test suite still use `InMemorySaver`. `build_graph`
  takes an optional checkpointer and defaults to it, and the sqlite import is
  lazy — so `pip install -r requirements.txt && python main.py` needs nothing
  extra.

## Deciding twice is a 409, not a second refund

Two reviewers opening the same queue item is the obvious race. `Command(resume=…)`
on a thread that is **not** waiting does not fail politely — it drives the
thread onward from wherever it currently sits. So `/decide` checks for a pending
interrupt first and returns `409 Conflict` if there is none.

`GRAPH_LOCK` closes the narrower window where two requests pass that check
concurrently. At this scale a lock is the honest answer; a multi-process
deployment would need the check and the resume to be atomic in the store
instead.

## Polling, not websockets

The queue is polled every 2s. A refund pauses *inside* a graph run, so there is
nothing to subscribe to without introducing a broker — and Phase 5 (Celery +
Redis) is still deliberately deferred for exactly the reason CLAUDE.md gives:
no genuinely slow tool call exists yet. Polling is right until one does.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, and whether the graph is in TEST_MODE or LIVE_MODE |
| POST | `/api/threads` | Mint a thread id |
| GET | `/api/threads` | Conversation list, flagged with `awaiting_approval` |
| GET | `/api/threads/{id}` | Full conversation + any pending approval |
| POST | `/api/threads/{id}/messages` | Send a customer message; returns replies added this turn |
| GET | `/api/escalations` | The queue, across all conversations |
| POST | `/api/escalations/{id}/decide` | Approve or deny; `409` if not waiting |

`new_messages` is sliced differently on the two write endpoints: a customer
message appends a `HumanMessage` that the UI already rendered, so it is skipped;
a resume appends nothing of the sort, so nothing is skipped. Getting this wrong
silently hides the refund reply — it did, during development.

## Running it

```bash
pip install -r requirements-api.txt
uvicorn api:app --reload          # :8000

cd frontend && npm install && npm run dev    # :5173, proxies /api to :8000
```

## Not done

- **AuthN/AuthZ.** Any caller can approve any refund. Real deployment needs a
  reviewer identity, and that identity belongs in the audit note rather than
  the free-text field it is today.
- **Multi-process safety.** See the 409 discussion above.
- **The customer and reviewer share one console.** Convenient for a demo, and
  obviously wrong for production.
