# Phase 10 — Postgres and Redis, Both Optional

**Status:** implemented and tested against real services — PostgreSQL 16 and a
vanilla Redis 7. 96 tests. Both are opt-in: unset the env vars and the project
runs on SQLite with an in-process cache exactly as before.

| Variable | Effect when set |
|---|---|
| `DATABASE_URL` | Business data **and** the LangGraph checkpointer move to Postgres |
| `REDIS_URL` | LLM response cache moves to Redis |
| `CACHE_TTL_SECONDS` | Cache entry lifetime (default 3600) |

## Why each earns its place

**Postgres** does two jobs SQLite cannot. It lets several API processes share
one escalation queue — a SQLite checkpoint file does not survive being written
by two workers — and it is what a deployment actually wants for durable pauses.

**Redis** caches model responses, and the justification is specific: *free-tier
rate limits*, which became the binding constraint in Phase 9. This system
re-asks the model the same questions constantly —

- the supervisor runs once per **hop**, so a two-specialist turn is three
  routing calls, and the second and third see the same user text;
- `risk_summary` sits above `interrupt()`, so Phase 6's ordering rule makes it
  re-run on every resume;
- demos and tests replay identical conversations.

All model calls use `temperature=0`, so identical input means identical output
and caching is sound rather than a correctness gamble.

**Nothing that reads the database is cached.** Caching order or refund state
would be a staleness bug *and* a security surface — a cached answer could
outlive the `customer_id` scoping that produced it. Only model responses,
keyed by full prompt plus model configuration.

## Dual-mode is one function, not two code paths

The only real difference between SQLite and Postgres here is the placeholder
style: `?` versus `%s`. Queries stay written with `?` and `db._sql()` translates
in one place. `psycopg`'s `dict_row` makes rows behave like `sqlite3.Row`, so
`dict(row)` works either way and nothing downstream knows which backend it got.

Two things that did need per-backend handling:

- `PRAGMA table_info` is SQLite-only, so the refund-column check asks
  `information_schema.columns` on Postgres.
- `sqlite3.Row` has no `.values()`. Both backends support *string* indexing, so
  scalars are read via an aliased column (`SELECT COUNT(*) AS n`).

`describe_backend()` returns the bare string `"postgres"` and never the URL,
because the URL carries the password and it is surfaced on `/api/health`.

## Why not langchain-redis

Its `RedisCache` requires **Redis Stack**. On a vanilla server it fails with:

```
ResponseError: unknown command 'JSON.GET'
```

Most free managed Redis tiers, the `redis:7-alpine` image and a plain
`apt install redis-server` are all vanilla. Forty lines of `BaseCache` runs
everywhere instead, and the project keeps one fewer dependency.

## Two bugs worth recording

**A cache that dropped `tool_calls` would break the tool loop only on the
second sighting of a prompt.** The natural implementation caches the
`ChatGeneration` wrapper, but rehydrating that needs a wide `allowed_objects`
allowlist. Storing the **message** and rebuilding the wrapper on read keeps
`allowed_objects="messages"` viable — so a shared or compromised Redis can
never be used to instantiate arbitrary classes — *and* preserves tool calls.
`test_tool_calls_survive_the_cache` pins it.

**The test suite silently refunded real rows in Postgres.** The autouse fixture
redirected `db.DB_PATH`, which is meaningless once `DATABASE_URL` is set — so
on any machine that had run the Postgres setup, `pytest` pointed the whole suite
at the real database, mutated it, and failed 9 tests from cross-test leakage.
The fixture now pins `db.BACKEND` to SQLite as well as the path, and the
Postgres tests opt out explicitly via their own fixture. Without that opt-out
they would have quietly tested SQLite while claiming Postgres, which is worse
than skipping.

Those Postgres tests also reset refund state before and after themselves. A
transaction rollback would be tidier, but `db.py` opens and commits its own
connections — correct for the app, and it simply means the test cleans up.
Verified repeatable: three consecutive runs, 96 passed, database left clean.

## Degradation

Unreachable Redis prints one line and falls back to the in-process cache. A
cache is an optimisation; losing it must never take the system down. There is a
test asserting exactly that against `redis://127.0.0.1:1`.

Postgres is deliberately **not** treated that way. If `DATABASE_URL` is set and
the server is unreachable, the app fails loudly — silently serving a different
database than the operator configured is far worse than refusing to start.

## Not done

- **No connection pooling.** Every query opens a connection. Fine at demo
  scale, wrong for production; `psycopg_pool` is the answer.
- **No migrations.** `init_db()` creates tables and adds missing columns; it is
  not Alembic and should not be mistaken for it.
- **Cache is not invalidated on model change.** The key includes model
  configuration, so switching models misses rather than serving stale answers —
  correct, but old entries linger until TTL.
- **A 429 still degrades silently** (carried over from Phase 9). The cache makes
  it less likely; it does not make it visible.
