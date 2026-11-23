# Agent Orchestration

[![CI](https://github.com/cruelkratos07/Agent-Orchestration/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/cruelkratos07/Agent-Orchestration/actions/workflows/tests.yml)

A LangGraph demo for e-commerce support. A supervisor routes conversations to order lookup, policy and refund specialists. The review console shows which agents handled a turn and lets a reviewer resolve refunds paused by the graph.

![Order Support Console showing a customer conversation and a pending refund review](docs/img/console-dark.png)

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). The console also requires Node.js 22.12+.

```sh
git clone https://github.com/cruelkratos07/Agent-Orchestration.git
cd Agent-Orchestration
uv run --locked --no-dev order-support-demo
```

The demo runs without an API key and uses a fresh, seeded SQLite file on each default run.

### Web console

Start the API from the repository root.

```sh
uv run --locked --extra api uvicorn agent_orchestration.api:app --reload
```

In a second terminal, start the frontend.

```sh
cd frontend
npm ci
npm run dev
```

Open the console at [localhost:5173](http://localhost:5173) or the interactive API docs at [localhost:8000/docs](http://localhost:8000/docs).

Try **Verify as Rohit**, then send **my camera ORD1003 arrived damaged, I want a refund**. The policy specialist replies, the refund specialist pauses, and the request appears in the review queue. Approve or deny it to resume the conversation.

The console uses synthetic customer data and simulates refund writes in the demo database. Treat the API as a local demo. Demo identifiers establish graph context, and the HTTP routes have no user or reviewer authentication.

## How it works

- **LangGraph** verifies the demo identity, routes each turn and checkpoints pending approvals.
- **LangChain Core** defines tools that receive the customer ID from graph state. The SQL layer scopes order reads and refund writes to that customer.
- **FastAPI** serves conversation and review endpoints. **React, TypeScript and TanStack Query** power the console.
- **SQLite** and an in-process model cache are the defaults. PostgreSQL, Redis and model providers are optional extras.

| Path | Contents |
| --- | --- |
| [`src/agent_orchestration/`](src/agent_orchestration/) | Graph, specialists, tools, data layer, API and CLI demo |
| [`frontend/`](frontend/) | Support and review console |
| [`tests/`](tests/) | Routing, order isolation, approvals and infrastructure tests |
| [`docs/`](docs/) | Architecture and archived design notes |

See the [architecture guide](docs/architecture.md) for the graph and data boundaries. The [development guide](CONTRIBUTING.md) covers checks, optional services, model providers and migration from the old commands.

## License

[MIT](LICENSE). The verification-before-routing pattern was adapted from [ANI-IN/Multi-Agent-Customer-Support](https://github.com/ANI-IN/Multi-Agent-Customer-Support).
