# n8n Lead DB Pipeline — deduplicates leads by email, preserves records across restarts

**Problem it solves:** leads arrive by webhook as messy payloads. Teams re-type
them into spreadsheets, duplicates slip in, failed validations vanish silently,
and nobody can answer "did we actually save that lead?"

**This workflow:** a POST webhook → validation → parameterized database write
→ immediate JSON receipt, plus a status endpoint that shows counts, recency and
duplicates at any time.

**Availability:** source + workflow are public in this repository. The store API
and `/status` endpoint run **locally only** (127.0.0.1) and contain synthetic
sample data — they are not exposed to the public internet. The screen demo
below shows the working local instance.

## What it demonstrates (end-to-end, all executed)

| Acceptance check | Evidence |
|---|---|
| Valid lead persists | `{"ok":true,"stored":{"id":8,"outcome":"created"}}` |
| Duplicate by email never becomes a second row | same email → `outcome:"duplicate"`, same id (sequential submissions tested) |
| Invalid email rejected cleanly | `{"ok":false,"error":"invalid email format"}` — no row written |
| Malformed payload | rejected at webhook (422 by body parser), pipeline unaffected |
| SQL-injection payload stored as inert data | `Robert'); DROP TABLE leads;--` saved as a name; table intact |
| Restart persistence | server killed + relaunched → all rows intact, pre-restart duplicates still detected |
| Status visibility | `GET /status` → counts by status, 5 most recent, server time |

## Architecture

```
n8n webhook (POST /webhook/lead)
  → Code node: normalize + validate email shape
  → HTTP Request → local store API (127.0.0.1:8765/store)
      → Python helper: SQLite with ?-parameterized SQL
      → UPSERT on email: created / duplicate outcomes
  → n8n responds with the store receipt (ok/error)
GET /status → live counts + recent leads
```

**Why parameterized writes matter:** the SQL layer uses `?` placeholders —
a lead named `Robert'); DROP TABLE leads;--` is stored as text. No lead field
is ever interpolated into a shell command or an SQL string.

**Why the store API is separate:** n8n's Execute Command node hung on shell
quoting in this environment; moving the database boundary into a reviewed
Python helper eliminated shell interpolation entirely and made failures
explicit JSON responses (never hanging requests).

## Setup (fresh machine)

1. `python3 scripts/lead_store_server.py` (creates/uses `db/leads.db`)
2. Import `workflows/lead-db-pipeline.json` into n8n, activate it
3. POST a lead to `http://localhost:5678/webhook/lead`
4. `curl http://localhost:8765/status`

## Limits (stated honestly)

- Independent demonstration, not a client deployment.
- SQLite local file — the pattern (parameterized upsert, status endpoint)
  transfers to Supabase/Postgres/BigQuery, but no cloud DB is wired here.
- No auth/roles, no enrichment APIs, no email sending — scoped out deliberately.
- Validation covers email presence/format; field-level rules are client-specific.

## Week 2 extension: approval-gated actions (AI proposes → human approves → one execution)

The same store now supports tasks that require human sign-off before execution:

```
POST /webhook/agent-task  (n8n → Hive LLM proposes action + reasoning)
  → POST /propose         (store records proposal + content hash, status=pending)
  → human approves        (POST /decide with the hash they saw)
  → POST /execute         (only from approved; executed once; result recorded)
```

**What is directly tested (executed evidence, not asserted):**

| Test | Result |
|---|---|
| Nothing executes without approval | execute before approval → `not approved yet` |
| Editing the proposed action invalidates approval | approve with stale hash → `hash mismatch`; correct-hash approve then works |
| Repeated approval clicks don't repeat the action | second approve → `already approved`; second execute → `already executed at <timestamp>` |
| Approval state survives a restart | server killed + relaunched → executed stays executed, pending list intact |
| Rejected proposals cannot execute | rejected → `rejected proposals cannot execute` |
| Expired requests cannot execute | pending older than 24h swept to `expired` → execute refused |
| Simultaneous executions | 4 concurrent executes on one approval → exactly 1 succeeded, 3 refused (`claimed by another request` / `already executed`); exactly one `executed_actions` row |
| Full journey | AI proposed a specific action → human approved (hash-validated) → executed action matches the approved action exactly → recorded in proposal + `executed_actions` |

**Known limits on the week-2 claim (stated before anyone asks):**

- Interruption *during* execution (crash between claim and effect) is not yet
  demonstrated; the atomic claim makes double-execution impossible through the
  API, but a mid-execution crash-rollback story is out of scope.
- This is a **local approval-workflow demonstration**, not protection against
  unauthorized approvers: approvals are bound to the proposal hash, not to an
  authenticated identity. Anyone with API access could approve — the gate
  ensures the approved content is what executes, and only once.
- The LLM proposes; it never executes. Execution is the store's, and only after
  a hash-validated human decision.

## Source

- Workflow export: `workflows/lead-db-pipeline.json`
- Approval journey: `workflows/approval-agent-journey.json`
- Store API: `scripts/lead_store_server.py`
- Schema: `db/schema.sql`

Contact: jawadsabir120@gmail.com · https://jadusaa.github.io/

## Known limits on the claim (stated before anyone asks)

- Tested with **sequential** submissions. Concurrent same-email submissions and a
  lost-response-then-retry cycle are not yet demonstrated; until then the honest
  claim is "deduplicates by email and preserves records across restarts," not
  "exactly once under concurrency."
- The `/status` endpoint is bound to 127.0.0.1 — deliberately not public. If it
  is ever exposed, it must get auth + real data hygiene first.
