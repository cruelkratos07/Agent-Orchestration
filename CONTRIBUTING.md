# Development

Run Python commands from the repository root. The dependencies and extras live in [pyproject.toml](pyproject.toml), and [uv.lock](uv.lock) records the resolved environment.

## Checks

```sh
uv run --locked --extra api pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv build
npm --prefix frontend ci
npm --prefix frontend run build
```

Core tests can run without the `api` extra. HTTP tests then skip. PostgreSQL and Redis tests run when their respective connection settings are present. GitHub Actions runs those tests with disposable service containers.

## Configuration

Copy [`.env.example`](.env.example) to `.env`, uncomment the settings you need, then pass `--env-file .env` to `uv run`. The default SQLite files are `support.db` and `checkpoints.sqlite` in the working directory. Override them with `SUPPORT_DB_PATH` and `CHECKPOINT_PATH`.

By default, the CLI uses a temporary SQLite file and the web console uses persistent storage. Setting `SUPPORT_DB_PATH` or `DATABASE_URL` runs the CLI against that database, where the scripted refunds change stored state.

### Model providers

Install the extra for one provider and set the matching key. Leave `LLM_PROVIDER` unset to detect a configured key, or set it to `none` to force deterministic mode. Use `LLM_MODEL` to select a model.

| Extra | Environment variable |
| --- | --- |
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| `groq` | `GROQ_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY`, optionally `OPENAI_BASE_URL` |

For example, put `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` in your local `.env` and run:

```sh
uv run --locked --env-file .env --extra gemini order-support-demo
```

### PostgreSQL and Redis

Start the local Compose services. Uncomment `DATABASE_URL` and `REDIS_URL` in `.env` to use their connection settings.

```sh
docker compose up -d --wait
uv run --locked --env-file .env --extra api --extra infra uvicorn agent_orchestration.api:app --reload
```

`DATABASE_URL` selects PostgreSQL for both business data and checkpoints. `REDIS_URL` selects the model response cache. Run `docker compose down` to stop the services while retaining their data.

## Command migration

Existing SQLite files continue to work when you launch from the repository root. If you start the app elsewhere, point the storage variables at the original files.

| Previous command | Current command |
| --- | --- |
| `pip install -r requirements.txt` and `python main.py` | `uv run --locked --no-dev order-support-demo` |
| `pip install -r requirements-api.txt` and `uvicorn api:app --reload` | `uv run --locked --extra api uvicorn agent_orchestration.api:app --reload` |
| `pip install -r requirements-infra.txt` | `uv sync --locked --extra api --extra infra` |

Pip installations also work with the package metadata. Use `python -m pip install -e ".[api]"` for the API and add extras such as `infra` or `gemini` as needed. Use uv to reproduce the locked environment.
