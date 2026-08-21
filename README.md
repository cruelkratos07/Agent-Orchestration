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
| LLM choosing its own `customer_id` | `customer_id` is an `InjectedToolArg`, so it is absent from the tool schema the model sees. The model cannot name a customer even once tools are bound to it. |

## Architecture

```
START -> verify -> [verified?] -> supervisor -> order_lookup -> END
                        |                    \
                        |                     -> policy      -> END
                        +-- not verified --> END (asks for identity)
```

- **verify** - resolves customer ID / email / phone against the database.
  Only place `customer_id` is ever written.
- **supervisor** - routes to a specialist based on intent. Holds no tools
  itself; it only delegates.
- **order_lookup** - order status and order listing. Reads `customer_id`
  from verified state, never from user text.
- **policy** - returns/refunds/exchanges. Needs no identity.

## Stack

- **Orchestration:** LangGraph (StateGraph, conditional edges, typed state
  with an `add_messages` reducer, `InMemorySaver` checkpointer keyed by
  `thread_id`)
- **LLM:** Anthropic Claude for intent routing (with a keyword-based
  test mode so the system runs without API keys)
- **Database:** SQLite, fully parameterized queries
- **Tests:** pytest, 14 tests covering the security invariants

## Running it

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Runs in TEST MODE without an API key. For live LLM routing:

```bash
export ANTHROPIC_API_KEY=your_key_here
python main.py
```

Tests:

```bash
python -m pytest tests/ -q
```

## Sample data

| Customer | Email | Phone | Orders |
|---|---|---|---|
| 1 Ananya Sharma | ananya.s@example.com | +919876543210 | ORD1001, ORD1002 |
| 2 Rohit Verma | rohit.v@example.com | +919812345678 | ORD1003, ORD1005 |
| 3 Meera Nair | meera.n@example.com | +919900112233 | ORD1004 |

Try verifying as customer 1, then asking for ORD1003 - it belongs to
customer 2 and will not be returned.

Identity and question can also arrive in the same message
("I'm customer 1, where is ORD1002?") - the verify gate runs first and the
specialist still sees the original question, not the verification reply.

## Project structure

```
pyproject.toml - pytest config (pythonpath, testpaths)
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
- [ ] Phase 3 - semantic memory (ChromaDB) for recall of similar past queries
- [ ] Phase 4 - multi-step delegation; refund processing specialist
- [ ] Phase 5 - async execution (Celery + Redis) for slow external calls
- [ ] Phase 6 - human-in-the-loop escalation for high-value refunds
- [ ] Phase 7 - React frontend with agent activity and escalation queue

## Credits

The identity-verification-before-routing pattern was adapted from
[ANI-IN/Multi-Agent-Customer-Support](https://github.com/ANI-IN/Multi-Agent-Customer-Support),
reimplemented here for the order support domain.
