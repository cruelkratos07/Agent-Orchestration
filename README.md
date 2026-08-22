# Order Support Agent System

A multi-agent customer support system for e-commerce order queries, built
with LangGraph. A supervisor agent routes verified customers to specialist
agents backed by a real database.

## The problem

Support systems sit between two conflicting pressures: customers ask in
free-form natural language, but answers about orders and money must be
exact and must never cross between accounts. A single LLM with tools
attached will eventually leak one customer's data to another, because the
only thing stopping it is a prompt asking it nicely not to.

This project solves that structurally instead.

| Risk | How the architecture prevents it |
|---|---|
| Cross-customer data leak | `customer_id` is scoped into the SQL `WHERE` clause of every order query. Wrong customer returns `None`, not data. |
| Identity spoofing via chat | `customer_id` is write-once, set only by `verify_node` after a real DB lookup. Later user claims cannot change it. |
| Unverified access to order data | The verification gate sits between START and the supervisor. There is no edge that reaches an order tool without passing it. |
| SQL injection through LLM output | Every query is parameterized. No f-strings or concatenation against any input. |
| Order IDs misread as customer IDs | Identifier extraction is deliberately narrow; `ORD1002` cannot be parsed as customer 1002 (regression-tested). |
| LLM choosing its own `customer_id` | `customer_id` is an `InjectedToolArg`, absent from the schema the model sees. Tools are now genuinely bound to the model, and `customer_id` is injected at execution time - a tool call arriving with one is discarded and overwritten. **The model can call tools but structurally cannot choose whose data it reads.** |
| Model naming data it was not offered | `resolve_order_reference` validates the model's answer against a customer-scoped order list. Anything else falls back. The model suggests; the code verifies. |
| Cross-account **mutation** | The refund `UPDATE` carries `customer_id` in its own `WHERE` clause. Ownership is not checked by a preceding `SELECT`, so there is no time-of-check/time-of-use gap. |
| Double refunds | `status` and `refund_status` are in the `UPDATE`'s `WHERE` clause, so a repeat attempt changes zero rows. Idempotency is enforced by the statement, not by a re-read. |
| A turn looping forever | Three independent stops: a refund is terminal, a specialist answers at most once per turn, and a hard cap of 3 hops. An unbounded loop-back raises `GraphRecursionError`, so this is not optional. |
| Money moving without review | High-value and damage-claim refunds `interrupt()` before any write. The write sits strictly below the interrupt, because everything above it re-runs on resume. |
| A malformed approval being read as "yes" | `normalize_decision` treats anything that is not a clear approval - `None`, `""`, a dict without `approved` - as a denial. |
| An LLM approving its own refund | `approved` is an `InjectedToolArg`, like `customer_id`. It is absent from the schema the model sees. |

## Architecture

```
START -> verify -> [verified?] -> supervisor -> order_lookup --+
                        |                    |-> policy ------+
                        |                    |-> refund ------+
                        |                    |                |
                        |                    +<---------------+
                        |                    |
                        |                    +-- FINISH --> END
                        +-- not verified --> END (asks for identity)
```

Specialists hand control back to the supervisor rather than ending the turn,
so one message can require several of them. `"my camera ORD1003 arrived
damaged, I want a refund"` runs **policy** (what the damaged-item rule
allows), then **refund** (act on it), then the supervisor closes the turn.

- **verify** - resolves customer ID / email / phone against the database.
  Only place `customer_id` is ever written.
- **supervisor** - routes to a specialist based on intent. Holds no tools
  itself; it only delegates.
- **order_lookup** - order status and order listing. Reads `customer_id`
  from verified state, never from user text.
- **policy** - returns/refunds/exchanges. Needs no identity.
- **refund** - issues refunds. The only node that writes. High-value or
  damage-claim refunds pause the graph and wait for a human to approve.

## Stack

- **Orchestration:** LangGraph (StateGraph, conditional edges, typed state
  with an `add_messages` reducer, `InMemorySaver` checkpointer keyed by
  `thread_id`)
- **LLM:** provider-agnostic - Gemini, Groq, Claude, or anything
  OpenAI-compatible. Used for intent routing, fuzzy order resolution, grounded
  policy answers, reviewer risk briefs, and real tool calling via `bind_tools`.
  Every call has a deterministic fallback, so the whole system runs with no API
  key at all.
- **Database:** SQLite by default, PostgreSQL when `DATABASE_URL` is set -
  same parameterized queries, same invariants, verified against both
- **Cache:** Redis for LLM responses when `REDIS_URL` is set, in-process
  otherwise. Cuts free-tier usage; nothing database-backed is ever cached
- **API:** FastAPI over the compiled graph, with a durable checkpointer
  (Postgres or SQLite) so a pending approval outlives a restart
- **Frontend:** React + TypeScript (Vite), agent activity feed + escalation queue
- **Tests:** pytest, 96 tests. LLM paths use a stub chat model; Postgres and
  Redis tests skip unless those services are configured, so a bare clone
  still runs the whole suite green.

## Running it

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Runs deterministically with no API key. To use a model, install one provider
and set its key - nothing else to configure:

```bash
pip install langchain-google-genai      # Gemini free tier
export GEMINI_API_KEY=...               # aistudio.google.com/apikey
python main.py                          # -> LIVE MODE - gemini:gemini-3.5-flash
```

Groq, Claude and any OpenAI-compatible endpoint (OpenRouter, Cerebras, local
Ollama) work the same way - see `requirements-llm.txt`. `LLM_PROVIDER` picks
one explicitly, `LLM_MODEL` overrides the model name, and `LLM_PROVIDER=none`
forces the deterministic path even with a key present.

Nothing about the security model is vendor-specific: `InjectedToolArg` is a
`langchain-core` feature, so `customer_id` stays invisible to the model
whichever provider is in use.

Tests:

```bash
python -m pytest tests/ -q
```

Optional Postgres + Redis (the CLI demo needs neither):

```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=support postgres:16
docker run -d -p 6379:6379 redis:7-alpine

pip install -r requirements-infra.txt
export DATABASE_URL=postgresql://postgres:pw@localhost:5432/support
export REDIS_URL=redis://localhost:6379
python main.py      # -> data: postgres   cache: redis
```

Vanilla Redis is enough; Redis Stack is not required.

Web console (optional - the CLI demo needs none of this):

```bash
pip install -r requirements-api.txt
uvicorn api:app --reload                      # http://127.0.0.1:8000

cd frontend && npm install && npm run dev     # http://127.0.0.1:5173
```

The console shows which specialist produced each line, and a queue of refunds
paused for human approval across every conversation. Approving one resumes that
graph exactly where it stopped.

## Sample data

| Customer | Email | Phone | Orders |
|---|---|---|---|
| 1 Ananya Sharma | ananya.s@example.com | +919876543210 | ORD1001, ORD1002, ORD1006 |
| 2 Rohit Verma | rohit.v@example.com | +919812345678 | ORD1003, ORD1005 |
| 3 Meera Nair | meera.n@example.com | +919900112233 | ORD1004, ORD1007 |

Order states matter now that refunds exist: only **Delivered** orders are
refundable. ORD1006 (Rs 2,499, customer 1) is below the approval threshold and
refunds automatically; ORD1003 (Rs 12,999, customer 2) is above it and gets
flagged for a human. Both sides of that branch are reachable on purpose.

Try verifying as customer 1, then asking for ORD1003 - it belongs to
customer 2 and will not be returned.

Identity and question can also arrive in the same message
("I'm customer 1, where is ORD1002?") - the verify gate runs first and the
specialist still sees the original question, not the verification reply.

## Project structure

```
pyproject.toml - pytest config (pythonpath, testpaths)
api.py       - FastAPI over the graph (threads, messages, escalation queue)
frontend/    - React + TS console (Vite)
llm.py       - provider selection + every model call, each with a fallback
cache.py     - LLM response cache (Redis or in-process)
docs/        - phase 2 audit, phase 4 design, phase 6/7/8 notes
db.py        - SQLite schema, seed data, parameterized queries
state.py     - AgentState TypedDict (messages, routing, identity)
verify.py    - identity extraction + verification gate
tools.py     - customer-scoped order tools, public policy tool
agents.py    - supervisor + specialist nodes
graph.py     - StateGraph wiring
main.py      - multi-turn demo conversations
tests/       - security invariant tests (test_security.py)
```

## Roadmap

- [x] Phase 1 - supervisor delegation, specialist agents, tool isolation
- [x] Phase 2 - real database, identity verification, security tests
- [x] Phase 4 - multi-step delegation; refund processing specialist
- [x] Phase 6 - human-in-the-loop escalation for high-value refunds
- [ ] Phase 3 - semantic memory (ChromaDB) for recall of similar past queries
      (deferred: recalling how a similar past ticket was resolved is a real
      use for embeddings; choosing between three route labels is not)
- [x] Phase 7 - React console: agent activity feed + escalation queue
- [x] Phase 8 - real LLM work: bound tools, fuzzy order refs, grounded policy
      answers, reviewer risk briefs
- [x] Phase 9 - provider-agnostic model layer (Gemini / Groq / Claude /
      OpenAI-compatible), runs free-tier or key-free
- [x] Phase 10 - PostgreSQL and Redis, both optional
- [ ] Phase 5 - async execution (Celery + Redis), once a genuinely slow tool
      call exists to justify it

## Credits

The identity-verification-before-routing pattern was adapted from
[ANI-IN/Multi-Agent-Customer-Support](https://github.com/ANI-IN/Multi-Agent-Customer-Support),
reimplemented here for the order support domain.
