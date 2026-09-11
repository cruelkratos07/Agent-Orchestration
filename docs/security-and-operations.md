# Security and operations

This is a production-oriented **reference implementation**, not a production
payment service. Refunds update seeded database records; no payment provider is
called. The CLI is a synthetic-data demonstration, not an authentication service.

## Two explicit web modes

The API defaults to authenticated mode. Without `SUPPORT_CREDENTIALS`, protected
endpoints return 503 rather than silently exposing customer data. `/api/live`
and `/api/auth/config` are public. Run only one API worker: graph operations use
a process-local lock, not a distributed execution coordinator.

For an isolated local demo, set `DEMO_MODE=true`. This bypasses authentication,
allows identity lookup by customer identifier, and combines customer/reviewer
roles. The UI displays a warning. Never expose demo mode to the internet.

For authenticated operation, provision one token per principal in a private
terminal:

```powershell
python scripts/create_access.py customer-ananya --role customer --customer-id 1
python scripts/create_access.py reviewer-jane --role reviewer
```

Keep each private token in a password manager. Combine the printed credential
records (not the private tokens) in a JSON array and supply it as the
`SUPPORT_CREDENTIALS` environment variable. For example, in PowerShell:

```powershell
$env:DEMO_MODE = 'false'
$env:SUPPORT_CREDENTIALS = '[{"subject":"customer-ananya","role":"customer","customer_id":1,"sha256":"REPLACE_WITH_PRINTED_DIGEST"},{"subject":"reviewer-jane","role":"reviewer","sha256":"REPLACE_WITH_PRINTED_DIGEST"}]'
uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1
```

The placeholders deliberately fail validation. Paste real printed digests to
run. Environment variables are not automatically loaded from `.env`. Rotate a
token by replacing its digest and restarting the process. Revocation is removal
of the credential record plus restart. Do not commit credentials or real data.

The browser login stores the private token only in memory, never localStorage.
Reloading the page requires login again. Customer identity is derived from the
authenticated principal, not from chat. Customer conversation ownership is
checked on every read and write; reviewers may read all registered conversations
and decide approvals, but cannot send messages as customers.

These are provisioned opaque API tokens, not customer password login, OTP, SSO,
or expiring sessions. Deployments handling real customer data need an identity
provider, TLS, credential lifecycle controls, rate limits, retention policy,
monitoring and an independent security review.

## Approval integrity

1. A high-value or damage-claim refund pauses before writing.
2. The reviewer submits a strict JSON boolean and the current interrupt ID.
3. A unique database insert claims that interrupt exactly once and records the
   reviewer, note, decision, action snapshot and timestamp.
4. Resume uses the reviewed order ID, not another model-generated guess, and
   checks customer and amount again. The refund tool independently enforces the
   escalation rule. The database update remains idempotent.
5. Decision processing records `completed` or `denied`. `completed` means the
   graph invocation returned successfully, **not** external payment settlement.

An exception records `needs_reconciliation`; a process killed after the claim
may leave `executing`. Neither may be automatically retried. Inspect the audit
record, graph checkpoint and order state together before an operator resolves
the incident. No automated reconciliation endpoint is provided. These stores
are not one atomic transaction, and this is not an exactly-once payment guarantee.

Audit rows persist in the application database, independently of checkpoints.
They are not tamper-proof against a database administrator. Protect access and
back up both application data and checkpoint storage together.

## Compatibility and limits

- Existing CLI and checkpoint-only conversations are not automatically assigned
  an owner. They remain on disk but are not exposed through the new web registry;
  create new web conversations. Never infer ownership from a claimed customer ID.
- API lists accept `limit` (1–100) and `offset`; escalation pagination currently
  scans that page of registered conversations, not a separately indexed pending
  queue. The UI shows the first 100 conversations. This is not a large-scale inbox.
- The API serializes graph work in one process. Keep one worker and one replica;
  scaling requires distributed coordination and an execution queue.
- Customer requests are capped at 8,000 characters and reviewer notes at 2,000.
- HTTP responses are marked `no-store` and have an `X-Request-ID`. Request logging
  excludes tokens and message bodies. Configure your server's logging/retention
  separately; checkpoints and decision records do contain conversation data.
- Policy-window prose is not a complete returns eligibility engine. Do not use
  these synthetic refund rules for actual commerce without implementing the
  business's full eligibility and evidence requirements.

## Verification

```powershell
python -m pytest tests -q
cd frontend
npm ci
npm run build
```

`tests/test_access.py` exercises HTTP authentication, cross-customer isolation,
role separation, stale/duplicate/malformed approvals, restart persistence,
atomic claims, changed-order protection and ambiguous-execution handling.
PostgreSQL/Redis integration tests require explicitly configured test services.
