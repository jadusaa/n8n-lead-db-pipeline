# n8n Lead DB Pipeline — every lead stored exactly once, with a status you can check

**Problem it solves:** leads arrive by webhook as messy payloads. Teams re-type
them into spreadsheets, duplicates slip in, failed validations vanish silently,
and nobody can answer "did we actually save that lead?"

**This workflow:** a POST webhook → validation → parameterized database write
→ immediate JSON receipt, plus a status endpoint that shows counts, recency and
duplicates at any time.

## What it demonstrates (end-to-end, all executed)

| Acceptance check | Evidence |
|---|---|
| Valid lead persists | `{"ok":true,"stored":{"id":8,"outcome":"created"}}` |
| Duplicate never becomes a second row | same email → `outcome:"duplicate"`, same id |
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

## Source

- Workflow export: `workflows/lead-db-pipeline.json`
- Store API: `scripts/lead_store_server.py`
- Schema: `db/schema.sql`

Contact: jawadsabir120@gmail.com · https://jadusaa.github.io/
