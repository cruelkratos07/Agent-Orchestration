# Order Support Orchestrator

An order-support application that routes customer requests across specialist
agents and pauses sensitive refunds for human review. It includes a LangGraph
workflow, FastAPI service, React console, SQLite/PostgreSQL persistence, and an
optional Redis-backed model cache.

The repository runs without an LLM provider by falling back to deterministic
routing, which keeps local development and CI reproducible.

![Reviewer console showing a refund awaiting approval](docs/img/console-dark.png)

## What it demonstrates

- Multi-step orchestration across verification, order, policy, and refund nodes
- Customer-scoped tools whose authorization arguments are hidden from the model
- Durable LangGraph checkpoints for conversations and interrupted workflows
- A reviewer queue with atomic decision claims and persistent audit records
- Strict separation between customer and reviewer permissions
- Optional model providers, PostgreSQL, and Redis without changing application code

Refunds in this repository update synthetic database records. No payment
provider is connected.

## Request flow

```text
START -> verify -> supervisor -> order lookup --+
                    |          -> policy -------+-> supervisor -> END
                    |          -> refund -------+
                    |                |
                    |                +-> human review (when required)
                    +-> END
```

The API supplies an authenticated customer ID to the graph. The CLI demo uses
identifier lookup against seeded data. Specialist tools scope every order query
and mutation by customer ID; the model cannot choose that argument.

Refunds at or above Rs 5,000 and requests reporting damage require review. A
review decision is tied to the current graph interrupt and the exact order
snapshot shown to the reviewer. Duplicate or stale decisions are rejected.

See [Architecture](docs/architecture.md) for the trust boundaries and failure
semantics, and [Security and operations](docs/security-and-operations.md) before
running the web application outside local development.

## Quick start

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The CLI uses SQLite, an in-process cache, and deterministic routing unless you
configure optional services or a model provider.

### Web console

Install the API and frontend dependencies:

```bash
pip install -r requirements-api.txt
cd frontend
npm install
```

For an isolated demo with synthetic identities:

```powershell
$env:DEMO_MODE = 'true'
uvicorn api:app --reload
```

In a second terminal:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`. Demo mode combines customer and reviewer access;
do not expose it publicly.

For separate customer and reviewer sessions, keep `DEMO_MODE` disabled and
provision credentials as described in the
[operations guide](docs/security-and-operations.md#two-explicit-web-modes).

## Optional services

The default setup needs neither Docker nor external infrastructure. To exercise
the PostgreSQL and Redis paths locally:

```bash
docker compose up -d
pip install -r requirements-infra.txt
```

Then configure:

```powershell
$env:DATABASE_URL = 'postgresql://orderagent:localdev@localhost:5432/support'
$env:REDIS_URL = 'redis://localhost:6379'
```

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Business data and LangGraph checkpoints | SQLite files |
| `REDIS_URL` | Shared model-response cache | In-process cache |
| `CACHE_TTL_SECONDS` | Cache entry lifetime | `3600` |
| `LLM_PROVIDER` | Explicit model provider, or `none` | Auto-detect |
| `LLM_MODEL` | Provider-specific model override | Provider default |
| `DEMO_MODE` | Enables the synthetic web demo | `false` |
| `SUPPORT_CREDENTIALS` | JSON credential records for web access | Unconfigured |

Model-provider examples are listed in [requirements-llm.txt](requirements-llm.txt)
and [.env.example](.env.example).

## Tests

```bash
python -m pytest tests -q
cd frontend
npm run build
```

PostgreSQL and Redis integration tests skip when those services are not
configured. CI runs the core, API, frontend, and service-backed suites.

## Repository layout

```text
agents.py       graph nodes and routing
api.py          authenticated FastAPI boundary
auth.py         credential validation and role checks
db.py           SQLite/PostgreSQL data access
graph.py        LangGraph topology and checkpoint setup
llm.py          provider adapters and deterministic fallbacks
records.py      conversation ownership and decision audit records
tools.py        customer-scoped order operations
frontend/       React reviewer/customer console
tests/          unit, workflow, API, and infrastructure tests
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development conventions and the
validation checklist.

The verification-before-routing pattern was adapted from
[ANI-IN/Multi-Agent-Customer-Support](https://github.com/ANI-IN/Multi-Agent-Customer-Support)
and reworked for this order-support domain.
