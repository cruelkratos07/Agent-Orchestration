# Phase 6 — Human-in-the-Loop Approval

**Status:** implemented. Verified against `langgraph==1.2.9`, Python 3.11.

## What it does

A refund that needs judgement stops the graph mid-turn and waits for a person.
The turn is not abandoned and re-driven from scratch — it is *paused*, and a
later `Command(resume=...)` continues from exactly where it stopped.

```
USER: please refund ORD1003
  [policy_agent] Refunds are accepted within 15 days of delivery...
  >> PAUSED for human approval: ORD1003 (Rs 12999) - at or above the Rs 5000 limit

REVIEWER: APPROVE - photo proof checked
  [refund_agent] Refund of Rs 12999 issued for ORD1003. Approved by a reviewer.
```

## Escalation triggers

Two, both in `tools.escalation_reason`:

| Trigger | Rationale |
|---|---|
| `amount >= REFUND_APPROVAL_THRESHOLD` (Rs 5,000) | Value risk. Carried over unchanged from Phase 4. |
| Request mentions damaged / broken / faulty / defective | Fraud risk, independent of value. ORD1007 at Rs 1,899 escalates on this alone. |

`escalation_reason` returns the *reason string*, not a bool, so the payload
shown to the reviewer and the wording of the eventual reply both explain
themselves rather than saying "flagged".

**Deliberately NOT a trigger: order not found.** CLAUDE.md listed it, but
paging a human to tell a customer their order ID is wrong is not a judgement
call — it is an answer. It stays a plain reply.

## The ordering rule — the thing that makes this correct

> **Everything above `interrupt()` runs a second time when the graph resumes.**

The node does not continue from the interrupt; it *restarts*. Verified by
spike — a counter placed above `interrupt()` reached 2 for a single approval:

```
side effects TOTAL: ['before-interrupt', 'before-interrupt', 'after-interrupt:approved']
                     ^ pause               ^ resume re-runs it
```

So `refund_node` is arranged strictly as:

1. **Above the interrupt — reads only.** Extract the order ID, load the order,
   decide whether it is refundable, compute the escalation reason. All pure,
   all safe to run twice.
2. **The interrupt.** Payload carries everything a reviewer needs to decide:
   order, item, amount, customer, reason, and the customer's own words.
3. **Below the interrupt — the write.** `process_refund` is called only here,
   and only when approved.

Put `db.refund_order()` above the interrupt and one approval issues two
refunds. Note the trap: the idempotent `UPDATE` from Phase 4 would *absorb*
the second write silently, so the bug would not show up as a double refund —
it would show up as nothing at all, until the day the write stopped being
idempotent. `test_approval_issues_exactly_one_refund` pins the timestamp to
catch it.

## Failing safe

`normalize_decision` accepts a dict (what a frontend would post), a bare bool,
or "approve"/"deny" (convenient in a demo or test). **Anything unrecognised —
`None`, `""`, a dict with no `approved` key — is treated as denial.** For an
operation that moves money, the default on a malformed decision must be to do
nothing. There is a test for each of those shapes.

`process_refund` also keeps its own high-value refusal, and `approved` is an
`InjectedToolArg` alongside `customer_id`. `refund_node` obtains approval
before calling the tool, so the check is redundant by design — but it means an
LLM cannot approve its own refund even if a future phase binds this tool to a
model and the node-level gate is bypassed.

## New invariants

10. A refund needing approval must not write anything before a human decides.
11. Everything above `interrupt()` must be side-effect free.
12. An unrecognised or malformed approval decision is a denial.

## Reading the queue — the Phase 7 seam

`graph.pending_approval(graph, config)` returns the payload a thread is waiting
on, or `None`. Phase 7's escalation queue is that function called across
threads. It lives next to the graph rather than in the frontend so the payload
shape has one owner.

## What Phase 6 did NOT need

No new state field, no `awaiting_approval` flag, no pending-decision table. The
checkpointer already persists the pause — that is what a checkpoint *is*. This
is the payoff for adding `InMemorySaver` back in the Phase 2 correctness pass:
Phase 6 turned out to be one `interrupt()` call and a reordering of one node.

For production the checkpointer would move to `SqliteSaver` or `PostgresSaver`
so a pause outlives the process. `InMemorySaver` means a restart forgets every
pending approval — fine for a demo, and a one-line change when it is not.
