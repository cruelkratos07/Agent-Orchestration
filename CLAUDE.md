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

## Status: Phase 2 complete
Real SQLite DB + identity verification gate + 11 security tests.
Runs in TEST_MODE (keyword routing) with no API key, LIVE_MODE with one.

Graph: START -> verify -> [verified?] -> supervisor -> specialist -> END

Files:
- db.py      — SQLite schema (customers, orders), parameterized queries
- state.py   — AgentState (messages, next, customer_id, customer_name, verified)
- verify.py  — identifier extraction + verification gate
- tools.py   — customer-scoped order tools, public policy tool
- agents.py  — supervisor_node, order_lookup_node, policy_node
- graph.py   — StateGraph wiring with verify gate in front
- main.py    — 4 multi-turn demo conversations
- tests/     — 11 pytest security invariant tests

SECURITY INVARIANTS (do not break these — they are tested):
1. customer_id is WRITE-ONCE, set only by verify_node after a DB lookup.
   No other node, tool, or LLM output may set or change it.
2. Every order query is scoped by customer_id in the SQL WHERE clause.
3. No path from START reaches an order tool without passing verify.
4. All SQL is parameterized. Never f-string or concatenate user input.
5. Order IDs (ORD1002) must never parse as customer IDs.

Known Phase 2 limitation (by design): specialists go straight to END,
no looping back to supervisor for multi-step tasks. That's Phase 4.

## Roadmap
2. DONE — real DB + identity verification
3. ChromaDB — embed past queries, let supervisor pull similar past
   cases before routing a new one
4. Real multi-step delegation — sub-agent can hand back to supervisor
   instead of always ending the turn; add Refund Processing specialist
5. Celery + Redis — async execution for slow tool calls
6. Human-in-the-loop escalation — trigger on: refund above a rupee
   threshold, damaged-item claims, order not found. Pause the graph,
   surface the decision, resume on approval.
7. React frontend — agent activity feed + pending escalations queue

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
