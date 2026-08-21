# Phase 4 Design — Real Multi-Step Delegation + Refund Specialist

**Status:** design only, no code written.
**Prerequisite:** the Phase 2 correctness pass (`pyproject.toml` present,
`pytest tests/ -q` = 14 passed). Phase 4 depends on `InjectedToolArg` and the
checkpointer from that pass.

Findings below marked *(tested)* were verified by spike against your pinned
`langgraph==1.2.9`, not assumed.

---

## 1. What Phase 4 has to prove

Today `supervisor_node` classifies once and every specialist edges to `END`.
That is a router, not a supervisor — an `if/elif` would do the same job. Phase 4
is done when a single user turn can legitimately require **two specialists**
and the supervisor sequences them without the user re-asking.

Target scenario, and the acceptance test for the whole phase:

> "My camera arrived damaged, I want a refund"

One turn, three hops: `policy` (what does the damaged-item rule allow) →
`refund` (apply it to a specific order) → `supervisor` decides done. That
sequence is impossible in the current topology.

**Non-goal:** an LLM planner. The supervisor stays a classifier; what changes
is that it gets called *again* after a specialist finishes, and can now see
what has already been done. Keep the increment small.

---

## 2. Edge topology

Current:

```
verify -> [verified?] -> supervisor -> order_lookup -> END
                                    -> policy       -> END
```

Phase 4:

```
verify -> [verified?] -> supervisor -> order_lookup -> supervisor
                                    -> policy       -> supervisor
                                    -> refund       -> supervisor
                                    -> FINISH       -> END
```

Two `add_edge(..., END)` calls become `add_edge(..., "supervisor")`, plus one
new node. *(tested: a specialist → supervisor back-edge compiles fine.)*

The verify gate is untouched. It still sits in front of everything, so
invariant 3 — no path from START reaches an order tool without passing
verify — survives unchanged. Worth stating explicitly in the commit message,
because "we added a loop" is exactly the kind of change that quietly breaks a
structural guarantee.

---

## 3. Termination — the part that actually needs designing

A loop-back edge with no stop condition does not hang harmlessly, it throws:

> *(tested)* `GraphRecursionError: Recursion limit of N reached without hitting
> a stop condition.`

So termination is not optional polish. Three separate decisions:

### 3a. The supervisor must be able to say "done"

`FINISH` already exists in `VALID_ROUTES` but is currently only reachable when
the *first* classification matches nothing. It now needs to be the normal exit
after work is complete. That means the supervisor needs to know what has
already happened this turn — see §4.

### 3b. A step guard, independent of the supervisor's judgement

Do not rely solely on the supervisor choosing `FINISH`. If routing misfires —
and §P0-3 in the audit showed how easily LIVE_MODE routing misfires — you want
a bounded failure, not a `GraphRecursionError` in a demo.

Add to `AgentState`:

```
steps: int          # specialist hops taken this turn
```

A plain `int` field is sufficient; the default overwrite semantics work because
the graph is sequential here. *(tested: a plain-int counter incremented in the
specialist and read in the supervisor terminates cleanly at the cap.)* No
`operator.add` reducer needed unless you later fan out to parallel specialists.

Cap it at **3**. Two is too tight for the acceptance scenario (policy →
refund), and anything above three means routing is broken rather than the task
being genuinely complex. On hitting the cap, the supervisor returns `FINISH`
and the graph should append a plain-language message rather than ending
silently — a turn that stops with no reply looks identical to a crash.

### 3c. Reset `steps` per TURN, not per thread — the non-obvious one

*(tested)* With a checkpointer, state persists per `thread_id`, so the counter
**carries across turns**. A spike that finished turn 1 at `steps = 3` began
turn 2 still at `3`, which would trip the cap immediately and make the second
message in every conversation a no-op.

This is a genuine trap and it will not show up in a single-turn test. `verify.py`
already runs first on every turn and already returns different dicts per branch,
so it is the natural place to reset:

- verified pass-through branch: return `{"steps": 0}` instead of `{}`
- both failure branches: include `"steps": 0` alongside the existing keys

Add a test that asserts turn 2 of a conversation can still reach a specialist.
That test is the whole reason this section exists.

---

## 4. What the supervisor needs to see

Currently `supervisor_node` reads only `last_user_text(state)`. To decide
`FINISH` it needs the work-so-far. Cheapest sufficient signal, in preference
order:

1. **`steps > 0` plus which specialists have run.** Derive the latter from
   `msg.name` on the messages this turn (`order_agent`, `policy_agent`,
   `refund_agent`). No new state field, no schema churn. Recommended.
2. A `completed: list[str]` field with an `operator.add` reducer. Cleaner to
   read, but it is new state to keep correct and it must also be reset per turn
   — a second instance of the §3c bug waiting to happen.

Go with (1). The information is already in `messages`; adding a parallel record
of it invites the two drifting apart.

For LIVE_MODE, the classification prompt needs a second input — a one-line
summary of what has already been done this turn — and an explicit instruction
that `FINISH` is correct when the request has been fully handled. Keep using
`normalize_route`, and keep `FINISH` as the fallback for unrecognised output:
under a loop-back topology an unrecognised label that fell through to a
specialist instead of to `FINISH` would be a live infinite-loop risk.

**TEST_MODE must terminate too.** This is easy to forget and it breaks the
no-API-key path that your CLAUDE.md requires stay working at every phase. The
keyword router currently matches on the user's text alone, which does not
change between hops — so it would route to `policy` forever. Rule: in
TEST_MODE, a specialist that has already run this turn is not eligible again;
when no eligible specialist matches, return `FINISH`.

---

## 5. Refund specialist

### Tool contract

```
process_refund(order_id: str,
               customer_id: Annotated[int, InjectedToolArg],
               reason: str) -> str
```

`customer_id` is `InjectedToolArg` for the same reason as the existing order
tools — invariant 6. This matters more here than anywhere else in the codebase:
this is the first tool that **writes**, so a model-chosen `customer_id` would be
a cross-account mutation rather than just a read leak.

### This is your first write path — three consequences

1. **Scope the UPDATE, not just the SELECT.** The `WHERE` clause needs
   `AND customer_id = ?` on the write itself. Reading the order first to check
   ownership and then updating by `order_id` alone is a TOCTOU hole and, more
   practically, one refactor away from dropping the check. Keep it in one
   statement.
2. **Idempotency.** A refund must not apply twice if the graph loops or a turn
   is retried. Make the status transition explicit and conditional —
   `WHERE status = 'Delivered'` (or whichever states are refundable) — so a
   second attempt affects zero rows and the tool reports "already refunded"
   rather than double-refunding. Check `cursor.rowcount` to distinguish the two.
3. **Tests stop being read-only.** The hermetic fixture from the P1 list
   (`monkeypatch db.DB_PATH` to a `tmp_path` file, seed per test) is now a
   prerequisite, not polish. Without it a refund test mutates your dev
   `support.db` and later runs behave differently.

### Schema

`orders` needs `refund_status TEXT` and `refunded_at TEXT` (nullable). Since
`init_db()` only creates tables `IF NOT EXISTS` and only seeds when empty, an
existing `support.db` will **not** pick up new columns. Simplest correct answer
at this stage: delete `support.db` and let it re-seed — it is gitignored and
demo-only. Do not build a migration system for seed data.

### Eligibility, and where Phase 6 plugs in

The refund node decides: refundable, not refundable, or **needs a human**. In
Phase 4, "needs a human" returns a message saying so. In Phase 6 that exact
branch becomes `interrupt()` — same condition, different mechanism. Design the
branch now, leave the escalation for later. Your CLAUDE.md already names the
triggers (amount above a rupee threshold, damaged-item claims, order not found);
put the threshold in one named constant so Phase 6 does not have to hunt for it.

---

## 6. New invariants Phase 4 introduces

Add to the CLAUDE.md list:

7. Every turn terminates. A turn either reaches `FINISH` by decision or by the
   step cap, and never raises `GraphRecursionError`.
8. A refund is applied at most once per order. Re-running the same refund
   affects zero rows.
9. Refund writes are scoped by `customer_id` in the `UPDATE` statement itself,
   not by a preceding read.

---

## 7. Test plan

Existing 14 must stay green — especially the four graph-level ones, since the
topology change is exactly what could break them.

New:

| Test | Asserts |
|---|---|
| `test_two_specialists_in_one_turn` | The damaged-item-refund turn hits both `policy_agent` and `refund_agent`, one user message |
| `test_turn_terminates_at_step_cap` | A supervisor stubbed to always route still ends, no `GraphRecursionError` |
| `test_step_counter_resets_between_turns` | §3c. Turn 2 on the same `thread_id` still reaches a specialist |
| `test_test_mode_does_not_loop` | No API key, specialists not re-eligible, turn ends |
| `test_refund_is_idempotent` | Second identical refund reports already-refunded, amount unchanged |
| `test_refund_cannot_touch_another_customers_order` | Customer 1 refunding ORD1003 changes zero rows |
| `test_refund_customer_id_not_model_fillable` | `"customer_id" not in process_refund.args` |
| `test_high_value_refund_is_flagged_not_processed` | Above threshold → flagged, DB unchanged (becomes the Phase 6 interrupt test) |

---

## 8. Suggested commit order

Each step leaves the suite green — worth it, because a loop-back edge plus a
new write path plus a schema change landing as one commit is very hard to debug.

1. `steps` in `AgentState` + reset in `verify.py` + the reset test. No topology
   change yet.
2. Supervisor learns to read work-so-far and return `FINISH`; step cap enforced.
   Still no loop-back — assert the supervisor's *decision* directly.
3. Flip the two specialist edges to `supervisor`. Termination tests now
   meaningful. TEST_MODE eligibility rule lands here.
4. Schema columns + `process_refund` tool + DB tests. No graph node yet.
5. `refund_node` + wire it in + the two-specialist acceptance test.

---

## 9. Scope guard

Explicitly **not** in Phase 4: ChromaDB, Celery/Redis, `interrupt()`, React. An
LLM planner is also out — the supervisor stays a classifier that gets called
repeatedly. TEST_MODE must still run the full demo with no API key at every one
of the five commits above.

---

## 10. Decisions for you

1. **Step cap of 3** — agree, or 2?
2. **Refundable states.** Only `Delivered`? What about `Shipped` (cancel in
   transit) and `Cancelled` (already refunded via a different path)?
3. **Refund threshold** for the flag-for-human branch. Your seed data spans
   Rs 1,999–12,999, so a threshold around Rs 5,000 makes ORD1002/ORD1003
   escalate and the rest auto-approve — which gives Phase 6 a live demo on both
   sides of the branch without editing data.
4. **Does `refund` need its own verify treatment?** It reads `customer_id` from
   verified state like `order_lookup` does, so no — but confirm you want a
   *write* gated by nothing stronger than the same identity check, versus
   re-confirmation for mutations. This is a defensible design decision either
   way and a good thing to have an answer for in an interview.

---

## Appendix — spike findings

Verified against `langgraph==1.2.9`, Python 3.11:

- Specialist → supervisor back-edge compiles and runs.
- Unbounded loop-back raises `GraphRecursionError: Recursion limit of N reached
  without hitting a stop condition` — not a hang.
- A plain-`int` `steps` field incremented in a node and read in the supervisor
  terminates cleanly; no custom reducer required for sequential hops.
- **A counter in state persists across turns on the same `thread_id`.** Turn 2
  started at `steps = 3`. Must be reset per turn.
- A node returning a key not present in the state schema is **silently
  ignored** — no error. So a typo'd key in a node's return dict is an invisible
  no-op. Relevant when adding `steps` to several return sites.
