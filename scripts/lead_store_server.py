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
import json, sqlite3, re, threading, hashlib as _hashlib, os as _os
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # keep demo logs clean; n8n logs the runs

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if self.path == "/pending":
            try:
                conn = db()
                # expire pending proposals older than 24h
                conn.execute("UPDATE proposals SET status='expired', decided_at=? "
                             "WHERE status='pending' AND created_at < datetime('now', '-1 day')",
                             (now(),))
                conn.commit()
                rows = conn.execute(
                    "SELECT id, task, token, proposal_hash, status, created_at "
                    "FROM proposals WHERE status = 'pending' ORDER BY id DESC").fetchall()
                conn.close()
                return self._json(200, {"ok": True, "pending": [
                    {"id": r[0], "task": r[1], "token": r[2], "hash": r[3],
                     "status": r[4], "created_at": r[5]} for r in rows]})
            except sqlite3.Error as e:
                return self._json(503, {"ok": False, "error": f"db: {e}"})
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


    def _handle_propose(self):
        try:
            return self._propose_inner()
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"internal: {e}"})

    def _propose_inner(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})
        task = str(body.get("task", "")).strip()
        proposed_action = str(body.get("proposed_action", "")).strip()
        reasoning = str(body.get("reasoning", "")).strip()
        if not task or not proposed_action:
            return self._json(400, {"ok": False, "error": "task and proposed_action required"})
        ts = now()
        token = _hashlib.sha256((task + ts + _os.urandom(8).hex()).encode()).hexdigest()[:16]
        ph = _hashlib.sha256((proposed_action + reasoning).encode()).hexdigest()
        conn = db()
        cur = conn.execute(
            "INSERT INTO proposals (task, proposed_action, reasoning, token, proposal_hash, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (task, proposed_action, reasoning, token, ph, ts))
        conn.commit()
        pid = cur.lastrowid
        conn.close()
        return self._json(200, {"ok": True, "id": pid, "token": token,
                                "proposal_hash": ph, "status": "pending",
                                "approve_url": f"/approve?token={token}",
                                "proposed_action": proposed_action, "reasoning": reasoning})

    def _handle_decide(self):
        try:
            return self._decide_inner()
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"internal: {e}"})

    def _decide_inner(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})
        token = str(body.get("token", "")).strip()
        decision = str(body.get("decision", "")).strip().lower()
        seen_hash = str(body.get("seen_hash", "")).strip()
        reason = str(body.get("reason", "")).strip()
        if decision not in ("approve", "reject"):
            return self._json(400, {"ok": False, "error": "decision must be approve or reject"})
        conn = db()
        row = conn.execute("SELECT id, proposed_action, reasoning, proposal_hash, status "
                           "FROM proposals WHERE token = ?", (token,)).fetchone()
        if not row:
            conn.close()
            return self._json(404, {"ok": False, "error": "unknown token"})
        pid, proposed_action, reasoning, current_hash, status = row
        if status != "pending":
            conn.close()
            return self._json(409, {"ok": False, "error": f"proposal already {status}"})
        # T2: editing the proposal invalidates approval — hash seen by approver
        # must match the stored hash
        if seen_hash and seen_hash != current_hash:
            conn.close()
            return self._json(409, {"ok": False, "error": "proposal changed since approval was shown (hash mismatch)"})
        ts = now()
        if decision == "approve":
            conn.execute("UPDATE proposals SET status='approved', decided_at=? WHERE id=?", (ts, pid))
            conn.commit(); conn.close()
            return self._json(200, {"ok": True, "id": pid, "status": "approved",
                                    "proposed_action": proposed_action,
                                    "execute_hint": "POST /execute with token"})
        conn.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=?", (ts, pid))
        conn.commit(); conn.close()
        return self._json(200, {"ok": True, "id": pid, "status": "rejected", "reason": reason})

    def _handle_execute(self):
        try:
            return self._execute_inner()
        except Exception as e:
            return self._json(500, {"ok": False, "error": f"internal: {e}"})

    def _execute_inner(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})
        token = str(body.get("token", "")).strip()
        conn = db()
        row = conn.execute("SELECT id, proposed_action, status, executed_at FROM proposals WHERE token = ?", (token,)).fetchone()
        if not row:
            conn.close()
            return self._json(404, {"ok": False, "error": "unknown token"})
        pid, proposed_action, status, executed_at = row
        # T1/T5: nothing executes without approval; rejected/expired cannot execute
        if status == "pending":
            conn.close()
            return self._json(409, {"ok": False, "error": "not approved yet"})
        if status == "rejected":
            conn.close()
            return self._json(409, {"ok": False, "error": "rejected proposals cannot execute"})
        if status == "expired":
            conn.close()
            return self._json(409, {"ok": False, "error": "expired proposals cannot execute"})
        # T3: repeated approval clicks don't repeat the action
        if status == "executed":
            conn.close()
            return self._json(409, {"ok": False, "error": f"already executed at {executed_at}"})
        if status != "approved":
            conn.close()
            return self._json(409, {"ok": False, "error": f"unexpected status {status}"})
        # EXECUTE the action — atomically claim it: a conditional UPDATE inside
        # an immediate transaction. Exactly one concurrent caller can flip
        # status approved→executed; the rest see 0 rows changed and are refused.
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE proposals SET status='executed', executed_at=?, "
            "execution_result=COALESCE(execution_result, execution_result) WHERE id=? AND status='approved'",
            (now(), pid))
        if cur.rowcount != 1:
            conn.rollback(); conn.close()
            return self._json(409, {"ok": False, "error": "proposal is no longer approved (claimed by another request)"})
        ea_cur = conn.execute(
            "INSERT INTO executed_actions (token, action, executed_at) VALUES (?, ?, ?)",
            (token, proposed_action, now()))
        ea_id = ea_cur.lastrowid
        ts = now()
        result = {"executed_action_id": ea_id, "action": proposed_action,
                  "executed_at": ts, "scope": "sample-data demo table"}
        conn.execute("UPDATE proposals SET execution_result=? WHERE id=?",
                     (json.dumps(result), pid))
        conn.commit(); conn.close()
        return self._json(200, {"ok": True, "executed": result})

    def do_POST(self):
        if self.path == "/propose":
            return self._handle_propose()
        if self.path == "/decide":
            return self._handle_decide()
        if self.path == "/execute":
            return self._handle_execute()
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


# ---- approval-gated agent extension (week 2) ----
def _ensure_proposal_schema():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        proposed_action TEXT NOT NULL,
        reasoning TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        proposal_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        decided_at TEXT,
        executed_at TEXT,
        execution_result TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS executed_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL,
        action TEXT NOT NULL,
        executed_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

_ensure_proposal_schema()

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("lead store listening on 127.0.0.1:8765", flush=True)
    server.serve_forever()
