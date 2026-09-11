# Contributing

## Development setup

Create a virtual environment and install the dependency set for the area you
are changing:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-api.txt
```

For frontend work:

```bash
cd frontend
npm install
```

PostgreSQL and Redis are optional. Use `docker compose up -d` when working on
their integration paths.

## Design rules

- Treat message text and model output as untrusted input.
- Keep customer scoping inside each database query or mutation.
- Keep authorization arguments out of the model-facing tool schema.
- Put no side effects above a LangGraph `interrupt()` call.
- Validate model output against application-owned data and allowlists.
- Preserve the deterministic path when adding or changing model behavior.
- Add a regression test for every authorization or refund-safety change.

Comments should explain constraints, tradeoffs, and surprising behavior. Avoid
development-history narration; Git already records when a change was made.

## Before opening a pull request

```bash
python -m pytest tests -q
cd frontend
npm run build
```

If the change touches PostgreSQL or Redis, also run the suite with
`DATABASE_URL` and `REDIS_URL` pointing to disposable local services.

Pull requests should describe the user-visible behavior, the failure mode being
addressed, and the tests that cover it. Do not include credentials, production
data, generated databases, checkpoint files, or frontend build output.
