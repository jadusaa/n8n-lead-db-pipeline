#!/usr/bin/env python3
"""
store_lead.py — parameterized lead store for the n8n lead-db-pipeline demo.

Input : base64(JSON) on stdin  (produced by the n8n Code node from the webhook body)
Output: single JSON line       {"id":N,"email":"...","outcome":"created"|"duplicate"}

SQL is fully parameterized (?-placeholders). No lead field is ever interpolated
into a shell command or an SQL string. A DB failure exits non-zero with a JSON
error on stdout, so the caller can return a clear 5xx instead of hanging.
"""
import sys, json, base64, sqlite3, os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "leads.db")

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def main():
    raw = sys.stdin.read().strip()
    try:
        payload = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as e:
        out({"outcome": "error", "error": f"bad payload encoding: {e}"})
        sys.exit(2)

    email = str(payload.get("email", "")).strip().lower()
    name = str(payload.get("name", "")).strip()
    company = str(payload.get("company", "")).strip()
    source = str(payload.get("source", "webhook")).strip()

    if not email:
        out({"outcome": "error", "error": "missing email"})
        sys.exit(3)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA busy_timeout = 4000")
        cur = conn.cursor()
        cur.execute(
            "SELECT id, duplicate_count FROM leads WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if row:
            lead_id, dup_count = row
            cur.execute(
                "UPDATE leads SET last_received_at = ?, duplicate_count = duplicate_count + 1, "
                "status = 'duplicate', name = ?, company = ?, source = ? WHERE id = ?",
                (now, name, company, source, lead_id),
            )
            conn.commit()
            out({"id": lead_id, "email": email, "outcome": "duplicate"})
        else:
            cur.execute(
                "INSERT INTO leads (email, name, company, source, status, "
                "first_received_at, last_received_at) VALUES (?, ?, ?, ?, 'new', ?, ?)",
                (email, name, company, source, now, now),
            )
            conn.commit()
            out({"id": cur.lastrowid, "email": email, "outcome": "created"})
        conn.close()
    except sqlite3.Error as e:
        out({"outcome": "error", "error": f"database write failed: {e}"})
        sys.exit(4)

if __name__ == "__main__":
    main()
