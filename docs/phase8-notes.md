# Phase 8 — Where the Model Actually Earns Its Place

**Status:** implemented. 72 tests (was 51). The live paths are exercised by a
stub chat model, so they run in CI with no API key.

## The problem this fixes

Before Phase 8 the project had **one** LLM call in it: a routing classifier
that a dict of keywords already handled. Everything else — verification, order
lookup, policy, refunds, the approval gate — was deterministic Python. The
architecture was the interesting part, and that framing is defensible, but
"where does the model earn its place?" had a thin answer for a project pitching
multi-agent orchestration.

Four jobs now, each something code cannot do well. All of them live in
`llm.py`, so there is exactly one file to read to know every place Claude is
used.

| Job | What code could not do |
|---|---|
| `route()` | (pre-existing) intent classification |
| `resolve_order_reference()` | "where's my camera?", "refund my last order" — regex simply failed |
| `policy_answer()` | apply the rule to *this* order's real dates instead of restating it |
| `risk_summary()` | a one-line brief for the human reviewing a refund |
| `run_order_tools()` | let the model choose and sequence tool calls |

## The headline: tool calling makes the Phase 4 guarantee load-bearing

`InjectedToolArg` was added in the Phase 2 correctness pass on the argument
that Phase 4's `bind_tools()` would otherwise let a model choose whose data to
read. Until now nothing exercised that — the claim was theoretical.

`run_order_tools` binds the order tools for real. The model picks the tool and
the order; `customer_id` is injected at execution time from verified state:

```python
args = dict(call.get("args") or {})
args.pop("customer_id", None)      # never trust it
args["customer_id"] = customer_id  # inject the verified one
```

The `pop` is not redundant with the schema omission. It covers prompt
injection, a confused model, and a future schema change that re-exposes the
field. The test that matters is
`test_tool_call_carrying_another_customer_id_is_overridden`: the stub model
asks for customer 2's camera while serving customer 1, and the tool returns
"no such order on this account" rather than the camera.

**The one-sentence version, which is the point of the whole project:** *the
model can call tools but structurally cannot choose whose data it reads.*

## The model suggests, the code verifies

`resolve_order_reference` hands the model a list of orders **already scoped by
`customer_id`**, and validates whatever comes back against that same list. A
model answering `ORD1003` while serving customer 1 is ignored and the
deterministic reading of the message is used instead. So the model can only
ever pick among rows this customer owns — it cannot widen its own access by
naming something it was not offered.

Same shape everywhere: nothing the model returns reaches the database without
being checked against a set the code built.

## Never worse than the deterministic path

Every function in `llm.py` has a deterministic fallback, taken when:

- there is no `ANTHROPIC_API_KEY` (TEST_MODE, unchanged since Phase 2),
- the API call raises — timeout, 503, rate limit,
- the model returns something empty or invalid.

So a flaky API degrades to Phase 7 behaviour rather than breaking a turn, and
`pip install -r requirements.txt && python main.py` still runs the whole demo
with no key. There is a test for each failure mode; `RuntimeError("503
overloaded")` from the stub must still route correctly.

Two fallbacks are worth naming because they are genuinely useful on their own:

- an explicit `ORD####` in the message is honoured without a model at all;
- "my last order" / "latest" / "most recent" resolves from the database, since
  `db.list_orders` is newest-first. Demo 11 shows this working with no key.

## Testing the live paths without a key

`llm.py` reads its model through `active_model()`, and `use_model()` swaps in a
stub. The stub implements only `bind_tools()` and `invoke()` — the entire
surface `llm.py` touches — and returns scripted `AIMessage`s, including ones
carrying `tool_calls`.

That means CI covers the tool-calling loop, the injection override, the
validation of model output, the loop bound, and every fallback. What it does
**not** cover is whether Claude actually returns sensible labels and summaries;
that needs a key and a human reading the output.

## Known costs

- `risk_summary` is called above the `interrupt()`, so it re-runs on resume
  (Phase 6's ordering rule). One wasted call per approval. Accepted rather than
  threading a cache through state for a one-line brief.
- With a key, the supervisor runs once per hop, so a two-specialist turn is
  three routing calls plus whatever the specialists make. Small prompts, but
  not free.
- `run_order_tools` is capped at `MAX_TOOL_TURNS = 4`. On exhaustion it says so
  and asks for an order ID rather than returning a half-answer.

## Try it

```bash
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # PowerShell
uvicorn api:app --reload
```

Then ask things regex cannot handle: "where's my camera?", "is my last order
still returnable?", "the speaker turned up broken, refund it". Without a key
the first two degrade to a full order list and the canned policy line — which
is exactly the comparison worth showing.
