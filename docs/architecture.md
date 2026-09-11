# Architecture

## Components

The application has four boundaries:

1. **HTTP access boundary** — `auth.py` validates provisioned bearer tokens and
   assigns either a customer or reviewer principal. `api.py` enforces role and
   conversation ownership checks.
2. **Workflow boundary** — `graph.py` wires verification, supervision, order,
   policy, and refund nodes. Checkpoints preserve conversation state and pending
   interrupts.
3. **Tool boundary** — `tools.py` requires an injected `customer_id` for every
   order read and write. The parameter is absent from the model-facing schema.
4. **Persistence boundary** — `db.py` stores customers and orders;
   `records.py` stores conversation ownership and review decisions. The graph
   checkpointer stores workflow state separately.

```text
Browser / API client
        |
        v
 FastAPI auth + ownership checks
        |
        v
 LangGraph workflow -----> model adapter / deterministic fallback
        |
        +-----> customer-scoped tools -----> orders database
        |
        +-----> checkpoint store
        |
        +-----> conversation + decision records
```

## Identity and authorization

Authenticated API requests do not extract identity from message text. The
customer ID is loaded from the matched credential record and passed to the
verification node through LangGraph configuration. The conversation registry
binds each thread to a subject and customer ID.

The CLI and explicit demo mode are different: they resolve an email, phone, or
seeded customer number from the conversation. That behavior is useful for the
sample workflow but is not authentication.

Order tools accept `customer_id` as an `InjectedToolArg`. LangChain removes the
argument from the schema presented to a model. The tool caller overwrites any
model-supplied value, and the SQL statement scopes both reads and writes to the
authenticated customer.

## Routing and termination

The supervisor asks at most three specialists to handle a user turn. A
specialist cannot run twice within the same turn, and a refund ends the turn.
These rules bound the supervisor loop even when model routing is invalid or
repetitive.

Model output is treated as a suggestion. Routes are normalized to an allowlist,
and model-resolved order references are checked against the customer's orders.
Each model call has a deterministic fallback for missing providers, invalid
responses, and provider errors.

## Refund review

`refund_node` performs only reads before calling `interrupt()`. LangGraph starts
the node again on resume, so a write above the interrupt could run twice.

The API handles review in this order:

1. Read the current interrupt and require its ID in the decision request.
2. Insert a unique `executing` decision record. Only one reviewer can claim it.
3. Resume the graph with the reviewed action snapshot.
4. Re-read the reviewed order and compare its ID, customer, and amount.
5. Run the customer-scoped, idempotent refund update below the interrupt.
6. Mark the decision `completed` or `denied`.

If graph execution raises after the claim, the record becomes
`needs_reconciliation`. It is not retried automatically because the application
cannot prove whether an external side effect occurred. A process terminated
between claim and completion can leave a record in `executing`; operators must
inspect the decision, checkpoint, and order state together.

The decision record and workflow checkpoint are separate durable writes. The
system does not claim an atomic or exactly-once payment transaction.

## Persistence

SQLite is the development default. When `DATABASE_URL` is present, business
data, ownership records, decisions, and graph checkpoints use PostgreSQL. Redis
is optional and caches only model responses; cache failure falls back to direct
execution.

The API uses a process-local graph lock because the SQLite checkpointer shares
one connection across request threads. Run one API worker. Supporting multiple
workers requires distributed workflow coordination rather than another local
mutex.

## Known limits

- Refunds modify synthetic order rows and do not contact a payment provider.
- Provisioned opaque tokens do not provide expiry, SSO, or self-service login.
- Decision records are durable but not tamper-evident to a database administrator.
- The review queue scans a bounded page of registered conversations.
- Policy text is illustrative and not a complete commerce eligibility engine.
- Existing checkpoint-only threads are not assigned ownership automatically.
