# Agent Orchestration System — Next Steps

**Verified against the actual code** on 2026-08-18: deps installed clean
(`langgraph==1.2.9`, `langchain-anthropic==1.5.2`, Python 3.11), `main.py`
runs correctly in TEST MODE, all 11 tests pass. So the good news first:
Phase 2 genuinely works as advertised on the happy path.

Everything below is something I reproduced, not something I inferred.

Also: `claude-sonnet-4-6` in `agents.py` is a **valid** model ID (it's in the
installed SDK's model list). No change needed there.

---

## P0 — Fix these to get it "working properly"

### 1. Verifying and asking in the *same message* silently drops the question

The most common thing a real user does — "Hi, I'm customer 1, where's my
order ORD1002?" — does not work.

```
USER: I'm customer 1, where is my order ORD1002?
  verified: True | next: 'FINISH'
  last msg: "Thanks Ananya Sharma, you're verified."
```

The question is never answered and nothing errors.

**Root cause:** `verify_node` appends an `AIMessage` to state. Then
`supervisor_node` (agents.py:28) and `order_lookup_node` (agents.py:54) both
read `state["messages"][-1]`, which is now the verification confirmation —
not the user's question. The supervisor keyword-matches against
*"Thanks Ananya Sharma, you're verified"*, finds nothing, and returns
`FINISH`.

The demo hides this because `main.py` always splits identity and question
into two separate turns.

**Fix:** both nodes should read the last **HumanMessage**, not the last
message. One small helper, used in both places:

```python
def last_user_text(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return m.content
    return ""
```

Then add a test for the combined-turn case — it's the single most valuable
test you don't currently have.

---

### 2. The tools expose `customer_id` as an LLM-fillable argument

This is the one to care about, because it undercuts the project's headline
claim. `tools.py` says *"Security lives in the tool signature, not in a
prompt politely asking the model to behave."* Right now the signature is
what **exposes** the parameter:

```
check_order_status args the LLM can fill: ['order_id', 'customer_id']
list_my_orders    args the LLM can fill: ['customer_id']

>>> check_order_status.invoke({"order_id": "ORD1003", "customer_id": 2})
'Order ORD1003 (Ultra HD Action Camera 4K, Rs 12999), status = Delivered, ...'
```

Today you're safe **only** because `agents.py` hardcodes `customer_id` from
state and never lets the model near it. But Phase 4 ("real multi-step
delegation") means `llm.bind_tools([check_order_status, ...])` — and at that
moment the model starts generating `customer_id` itself, and security
invariant #1 is gone. The structural guarantee you've documented isn't
structural yet; it's a convention in one file.

**Fix (do this before Phase 4, not during):**

```python
from typing import Annotated
from langchain_core.tools import tool, InjectedToolArg

@tool
def check_order_status(order_id: str,
                       customer_id: Annotated[int, InjectedToolArg]) -> str:
    ...
```

Verified this removes it from the model-visible schema:

```
After InjectedToolArg, LLM-fillable args: ['order_id']
```

(`langgraph.prebuilt.InjectedState` is the alternative if you'd rather pull
it from state directly. Both are available in your installed versions.)

Then add the test that makes the invariant real:

```python
def test_customer_id_is_not_model_fillable():
    assert "customer_id" not in check_order_status.args
    assert "customer_id" not in list_my_orders.args
```

---

### 3. LIVE_MODE routing is brittle enough to be effectively broken

`route_from_supervisor` (graph.py:26) does exact string equality on the LLM's
raw reply. Your prompt asks for the label *including the quote marks*
(`'order_lookup'`). Reproduced:

```
LLM said 'order_lookup'                    -> 'order_lookup'   OK
LLM said "'order_lookup'"                  -> '__end__'        DROPPED
LLM said 'policy.'                         -> '__end__'        DROPPED
LLM said 'Order_Lookup'                    -> '__end__'        DROPPED
LLM said 'The category is order_lookup'    -> '__end__'        DROPPED
```

Every failure is silent — the turn just ends with no answer. Three of those
four are things Claude plausibly returns.

**Fix:** normalize before matching (lowercase, strip quotes/punctuation/
whitespace), match against an explicit whitelist, and fall back to a known
default. Better still, constrain the output so it can't drift:

```python
route = llm.with_structured_output(Route).invoke(...)   # Literal[...] field
```

Also drop the quote marks from the prompt text — you're asking for the thing
that breaks the parser.

---

### 4. No checkpointer — and Phases 6 and 7 both hard-require one

```
graph.checkpointer: None
```

`main.py` and `test_security.py` both thread the entire state dict back in by
hand each turn. That's fine for a script, but:

- **Phase 6 (human-in-the-loop)** cannot work without it. `interrupt()`
  pauses the graph by *persisting* a checkpoint; with no checkpointer there
  is nothing to resume from.
- **Phase 7 (React frontend)** needs one `thread_id` per conversation —
  otherwise the frontend has to POST the whole message history every turn.

Both `interrupt`/`Command` and `InMemorySaver` are already present in your
installed `langgraph` 1.2.9, so this is a 3-line change now and a
refactor-everything change later:

```python
from langgraph.checkpoint.memory import InMemorySaver
return builder.compile(checkpointer=InMemorySaver())
# then: graph.invoke({"messages": [...]},
#                    config={"configurable": {"thread_id": "conv-1"}})
```

Once that's in, `main.py` stops hand-threading state and just passes a
thread_id — which is also a much better demo of "I understand LangGraph."

---

### 5. The README's test command doesn't run

Both `README.md` and `CLAUDE.md` say `tests/` and
`python -m pytest tests/ -q`. There is no `tests/` directory — the file is
`test_security.py` at the repo root:

```
$ python -m pytest tests/ -q
ERROR: file or directory not found: tests/
```

For a portfolio repo this is the first thing a reviewer copy-pastes, so it's
worth two minutes. Relatedly, `test_security.py:6` has a
`sys.path.insert(dirname(dirname(...)))` hack that only makes sense from
*inside* `tests/` — it currently inserts the parent of the repo root.

**Fix:** move the file to `tests/test_security.py` and delete the sys.path
hack (add a `conftest.py` or `pyproject.toml` with `pythonpath = ["."]`
instead). That makes the docs true and removes the hack in one move.

---

## P1 — Cleanup worth doing while you're in there

- **`verify.route_after_verify` is dead code that returns the wrong thing.**
  It returns the *string* `"END"`; the real sentinel is `"__end__"`.
  `graph.py` has its own correct copy, so nothing is broken today — but if
  anyone ever wires up the `verify.py` one it fails at compile. Delete it.
- **`main.py:23-25` is a no-op loop** (`for msg in ...: if ...: pass`).
  Leftover scaffolding; delete.
- **`requirements.txt` has it backwards.** `langchain==1.3.14` is pinned but
  never imported. `langchain-core` *is* imported directly everywhere but
  isn't pinned (it arrives transitively). Swap them so the pin matches the
  actual dependency.
- **`db.init_db()` runs as an import side effect** (graph.py:57). Importing a
  module shouldn't create a database file. Move it into `main.py` and a
  pytest fixture.
- **Tests run against your live `support.db`.** They're read-only today so
  they pass, but a fixture that points `db.DB_PATH` at a `tmp_path` file
  makes them hermetic — and you'll need that anyway once Phase 4 adds a
  refund specialist that *writes*.
- **Phone regex is loose.** `\+?[\d][\d\s\-]{8,}\d` matches any run of 10+
  digits, so a message containing a long order/invoice/tracking number could
  be treated as a phone identifier. Worth one test.
- **No git repo, LICENSE, `.env.example`, or CI** in the archive. A GitHub
  Actions job that runs the 11 security tests on push is very cheap
  credibility for an AI-Engineer portfolio — "the invariants are enforced by
  CI, not by me remembering" is a strong line in an interview.

---

## Suggested order

| # | Work | Why now |
|---|---|---|
| 1 | Fix README/`tests/` path (P0-5) | 2 minutes, first impression |
| 2 | Read last **HumanMessage** (P0-1) | Fixes the most common real user input |
| 3 | Normalize supervisor routing (P0-3) | Makes LIVE_MODE actually usable |
| 4 | `InjectedToolArg` + new test (P0-2) | Must land **before** Phase 4 |
| 5 | Checkpointer + `thread_id` (P0-4) | Must land **before** Phase 6/7 |
| 6 | P1 cleanup + CI | Portfolio polish |
| 7 | **Then** Phase 3 (ChromaDB) | Build on a correct base |

Items 4 and 5 are the ones that get materially more expensive the longer you
wait — they're both small edits today and both sit directly under the next
two roadmap phases.

One nuance on Phase 3 vs Phase 4 ordering: Phase 3 (semantic recall to
inform routing) only pays off if routing is trustworthy, and right now it
isn't (P0-3). Fixing routing first also gives you a real before/after story
for whether ChromaDB recall actually improves routing — which is a better
portfolio narrative than "I added a vector DB."
