#!/usr/bin/env python3
"""
lead_store_server.py — local HTTP API for the n8n lead-db-pipeline demo.

Endpoints:
  POST /store   body: JSON {email, name?, company?, source?}
                → parameterized SQLite insert/upsert (no SQL string building)
                → 200 {"ok":true,"id":N,"outcome":"created"|"duplicate"}
                → 400 validation error / 503 database error (never hangs)
  GET  /status  → JSON counts by status, last run, recent leads
  GET  /health  → {"ok":true}

Design notes:
- All SQL uses ?-placeholders (parameterized). Lead fields never touch a shell
  or an SQL string.
- ThreadingHTTPServer + per-request connection with busy_timeout: requests
  cannot hang on each other.
- Sample data only; no secrets, no real customer data.
"""
import json, sqlite3, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "leads.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA busy_timeout = 4000")
    return conn

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep demo logs clean; n8n logs the runs

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if self.path == "/status":
            try:
                conn = db()
                counts = dict(conn.execute(
                    "SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
                last = conn.execute(
                    "SELECT id, email, status, duplicate_count, last_received_at "
                    "FROM leads ORDER BY last_received_at DESC LIMIT 5").fetchall()
                total_runs = conn.execute(
                    "SELECT COUNT(*) FROM leads").fetchone()[0]
                conn.close()
                return self._json(200, {
                    "ok": True,
                    "counts": counts,
                    "total_leads": total_runs,
                    "recent": [
                        {"id": r[0], "email": r[1], "status": r[2],
                         "duplicate_count": r[3], "last_received_at": r[4]}
                        for r in last],
                    "server_time": now(),
                })
            except sqlite3.Error as e:
                return self._json(503, {"ok": False, "error": f"db: {e}"})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/store":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})

        email = str(body.get("email", "")).strip().lower()
        name = str(body.get("name", "")).strip()
        company = str(body.get("company", "")).strip()
        source = str(body.get("source", "webhook")).strip()

        if not email:
            return self._json(400, {"ok": False, "error": "missing email"})
        if not EMAIL_RE.match(email):
            return self._json(400, {"ok": False, "error": "invalid email format"})

        ts = now()
        try:
            conn = db()
            cur = conn.cursor()
            cur.execute("SELECT id, duplicate_count FROM leads WHERE email = ?", (email,))
            row = cur.fetchone()
            if row:
                lead_id, _ = row
                cur.execute(
                    "UPDATE leads SET last_received_at = ?, duplicate_count = duplicate_count + 1, "
                    "status = 'duplicate', name = ?, company = ?, source = ? WHERE id = ?",
                    (ts, name, company, source, lead_id))
                conn.commit()
                conn.close()
                return self._json(200, {"ok": True, "id": lead_id,
                                        "email": email, "outcome": "duplicate"})
            cur.execute(
                "INSERT INTO leads (email, name, company, source, status, "
                "first_received_at, last_received_at) VALUES (?, ?, ?, ?, 'new', ?, ?)",
                (email, name, company, source, ts, ts))
            conn.commit()
            lead_id = cur.lastrowid
            conn.close()
            return self._json(200, {"ok": True, "id": lead_id,
                                    "email": email, "outcome": "created"})
        except sqlite3.Error as e:
            return self._json(503, {"ok": False, "error": f"database write failed: {e}"})

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("lead store listening on 127.0.0.1:8765", flush=True)
    server.serve_forever()
