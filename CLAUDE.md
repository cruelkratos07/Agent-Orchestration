# Agent Orchestration System — Project Context

## Objective
Portfolio project for AI Engineer roles. Prove I can architect a
multi-agent system (supervisor delegation, persistent memory,
async execution, human-in-the-loop escalation) — not just prompt
a single agent with a couple of tools.

## Domain (locked in, do not change without asking)
Customer order support triage. A supervisor agent routes incoming
customer messages to one of two (soon three) specialists:
- Order Lookup — order status/tracking questions
- Policy — return/refund/exchange policy questions
- (Phase 4+) Refund Processing — actually issues refunds, this is
  the specialist most likely to need human-in-the-loop escalation

## Stack
- Orchestration: LangGraph (Python)
- LLM: Anthropic Claude (via langchain-anthropic)
- Memory: PostgreSQL (structured: conversation/routing logs) +
  ChromaDB (semantic: similar past query recall)
- Queue: Redis + Celery (added Phase 5, only once a genuinely slow
  tool call exists — don't add async complexity before it's needed)
- Frontend: React (not Streamlit — I already know React/TS well)

## Status: Phase 8 complete
Real SQLite DB + identity verification gate + 14 security tests.
Runs in TEST_MODE (keyword routing) with no API key, LIVE_MODE with one.

Correctness pass applied on top of Phase 2:
- Nodes read the last HumanMessage (state.last_user_text), not messages[-1].
  Previously "I'm customer 1, where is ORD1002?" verified and then silently
  dropped the question, because verify_node's own AIMessage was messages[-1].
- customer_id is Annotated[int, InjectedToolArg] in tools.py, so it is not in
  the schema the LLM sees. Required before Phase 4 binds tools to the model.
- supervisor routing goes through agents.normalize_route, which coerces the
  LLM label into VALID_ROUTES. Exact-match on the raw reply used to drop
  "'policy'", "Order_Lookup" and "The category is order_lookup" into END
  with no error, making LIVE_MODE unreliable.
- Graph compiles with InMemorySaver. Callers pass a thread_id and only the new
  message. Prerequisite for Phase 6 (interrupt() needs a checkpoint to resume
  from) and Phase 7 (one thread per frontend conversation).
- Tests live in tests/ so the documented `pytest tests/ -q` actually runs;
  sys.path hack replaced by pyproject.toml pythonpath.

Graph: START -> verify -> [verified?] -> supervisor -> specialist -> END

Files:
- db.py      — SQLite schema (customers, orders), parameterized queries
- state.py   — AgentState (messages, next, customer_id, customer_name, verified)
               + last_user_text() helper
- verify.py  — identifier extraction + verification gate
- tools.py   — customer-scoped order tools, public policy tool
- llm.py     — every Claude call + its deterministic fallback
- agents.py  — supervisor_node, order_lookup_node, policy_node, refund_node
- graph.py   — StateGraph wiring with verify gate in front, InMemorySaver
- main.py    — 5 multi-turn demo conversations (thread_id based)
- tests/     — 37 pytest security/termination/approval tests (hermetic temp DB)
- api.py     — FastAPI over the graph; GRAPH_LOCK serialises graph work
- frontend/  — React + TS console (Vite); polls /api/escalations every 2s
- docs/      — phase2-audit, phase4-design, phase6-notes, phase7-notes

PHASE 7 gotchas:
- /api/escalations/{id}/decide MUST 409 when the thread is not waiting.
  Command(resume=...) on a thread that isn't paused drives it onward from
  wherever it sits rather than failing — that is the two-reviewer race.
- new_messages slicing differs per endpoint: a customer message appends a
  HumanMessage the UI already rendered (skip 1); a resume appends none (skip 0).
  Getting this wrong silently hides the refund reply.
- build_graph(checkpointer=None) defaults to InMemorySaver and the sqlite
  import is lazy, so `pip install -r requirements.txt && python main.py` keeps
  working with no API extras. Keep it that way.

PHASE 8 (done): every LLM call lives in llm.py. Four jobs beyond routing:
resolve_order_reference, policy_answer, risk_summary, run_order_tools.

TWO RULES for anything added to llm.py:
1. EVERY function needs a deterministic fallback, taken on no-key, on
   exception, and on invalid output. TEST_MODE must keep working.
2. THE MODEL SUGGESTS, THE CODE VERIFIES. Anything the model returns that
   could reach the DB is validated against a customer-scoped set first.

Tool calling is real now (llm.run_order_tools uses bind_tools). customer_id is
popped from every tool call and re-injected from verified state. The pop is NOT
redundant with the schema omission - it covers prompt injection and future
schema drift. Do not remove it.

Live paths are tested with a stub model (tests/test_llm.py, class StubModel);
it implements only bind_tools + invoke. Keep it that way so CI needs no key.

SECURITY INVARIANTS (do not break these — they are tested):
1. customer_id is WRITE-ONCE, set only by verify_node after a DB lookup.
   No other node, tool, or LLM output may set or change it.
2. Every order query is scoped by customer_id in the SQL WHERE clause.
3. No path from START reaches an order tool without passing verify.
4. All SQL is parameterized. Never f-string or concatenate user input.
5. Order IDs (ORD1002) must never parse as customer IDs.
6. customer_id must never appear in the tool schema the LLM sees. It is an
   InjectedToolArg — the caller supplies it from verified state, never the
   model. This is what makes invariant 1 structural rather than a convention
   held up by agents.py.
7. Every turn terminates. No turn may raise GraphRecursionError.
8. A refund applies at most once per order; a repeat attempt changes 0 rows.
9. Refund writes are scoped by customer_id in the UPDATE statement itself,
   never by a preceding read (no time-of-check/time-of-use gap).
10. A refund needing approval writes nothing before a human decides.
11. Everything above interrupt() is side-effect free.
12. An unrecognised or malformed approval decision counts as a denial.
13. customer_id in a model-generated tool call is discarded and replaced with
    the verified one. The model never chooses whose data is read.
14. A model-named order ID is only honoured if it is in that customer's own
    order list.

PHASE 4 (done): specialists edge back to supervisor, not END, so one turn
can use several. New refund specialist - the only node that writes.

Termination rules (all three tested, do not remove any of them):
  1. A refund is terminal for the turn.
  2. A specialist answers at most once per turn.
  3. MAX_SPECIALIST_HOPS = 3 is a hard backstop.
An unbounded loop-back raises GraphRecursionError, it does not hang.

"What ran this turn" is DERIVED from messages since the last HumanMessage
(state.specialists_used), NOT stored in state. A counter in state persists
across turns on the same thread_id and would need a per-turn reset; forget it
and turn 2 of every conversation starts at the cap and silently does nothing.
Deriving it makes that bug impossible. Don't "simplify" this into a counter.

PHASE 6 (done): refunds needing judgement pause the graph via interrupt() and
resume on Command(resume=...). No new state field was needed - the checkpointer
added in the Phase 2 pass already persists a pause.

THE ORDERING RULE (do not break this):
  Everything ABOVE interrupt() runs a SECOND time when the graph resumes - the
  node restarts, it does not continue. Verified by spike. So refund_node is
  reads-only above the interrupt and the write is strictly below it. Moving
  db.refund_order() above the interrupt makes one approval issue two refunds,
  and the idempotent UPDATE would SWALLOW the second one - so the bug would be
  invisible until the write stopped being idempotent.

Escalation triggers (tools.escalation_reason): amount >= Rs 5,000, or the
request mentions damaged/broken/faulty/defective. "Order not found" is
deliberately NOT a trigger - it is an answer, not a judgement call.

Malformed approval decisions FAIL SAFE to denial. Anything that isn't a clear
approval does not move money.

Refund rules: only Delivered orders are refundable. Refunds >= Rs 5,000
(REFUND_APPROVAL_THRESHOLD in tools.py) are flagged for a human rather than
issued, and the check runs BEFORE the write so the DB is untouched. Phase 6
turns that exact branch into interrupt().

Seed data note: only Delivered orders are refundable, and the original seed
had exactly one Delivered order and it was above the threshold - auto-approval
was dead code. ORD1006 (Delivered, Rs 2,499) exists so both sides of the
branch are reachable. A test fails if a future seed edit breaks that.

## Roadmap
2. DONE — real DB + identity verification
4. DONE — real multi-step delegation + Refund Processing specialist
3. ChromaDB — DEFERRED, and reordered after 4 deliberately. Embeddings to
   choose between three route labels is a vector DB doing work a keyword
   match already does. Revisit when the use is recalling how a similar past
   ticket was RESOLVED, which is a real use for it.
6. DONE — human-in-the-loop escalation. Triggers: refund above the rupee
   threshold, damaged-item claims. Order-not-found deliberately excluded.
7. DONE — FastAPI (api.py) + React/TS console (frontend/). Escalation queue is
   list_thread_ids + pending_approval across threads. API uses SqliteSaver so a
   pending approval survives a restart; CLI and tests still use InMemorySaver.
5. Celery + Redis — STILL deferred, and the queue polling in the frontend is
   the honest stand-in until a genuinely slow tool call exists to justify it.

## My conventions
- Minimal diffs when editing existing code — don't restructure things
  I didn't ask you to restructure.
- Prefer clean/minimal over decorative — same as my UI preferences
  elsewhere (I default toward trimming features back, not adding more).
- Explain new concepts as we hit them in real code (I'm learning
  LangGraph from scratch alongside building this) — but don't repeat
  explanations for concepts already covered in this file's Phase 1 notes.
- Test-mode fallback (no API key needed) should keep working at every
  phase, so the whole thing stays runnable/demoable without live keys.
