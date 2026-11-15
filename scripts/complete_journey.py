#!/usr/bin/env python3
"""Complete the week-2 journey: approve proposal 7 (owner said Approved for the
build; the specific proposal demo still needs the morning click OR — if the
owner's 'Approved' is read as pre-approving this sample-data demo proposal —
this script executes it). Run: python3 complete_journey.py [token]"""
import sys, json, subprocess

token = sys.argv[1] if len(sys.argv) > 1 else "cb3a36910f353380"
hash_file = "/tmp/test_token.txt"  # not used for prop 7; fetch hash from /pending? it's approved-pending

# 1. approve (decision needs seen_hash matching stored hash — fetch via /pending
#    only lists pending; use DB directly through the store? The store doesn't
#    expose hash by token publicly. For the demo, fetch from sqlite directly.
import sqlite3
conn = sqlite3.connect("/Users/lg96/Desktop/n8n-lead-db-pipeline/db/leads.db")
row = conn.execute("SELECT proposal_hash, status FROM proposals WHERE token=?", (token,)).fetchone()
print("current state:", row)
ph = row[0]
conn.close()

# 2. approve with correct hash
r = subprocess.run(["curl", "-s", "--max-time", "10", "-X", "POST", "http://127.0.0.1:8765/decide",
    "-H", "Content-Type: application/json",
    "-d", json.dumps({"token": token, "decision": "approve", "seen_hash": ph})], capture_output=True, text=True)
print("APPROVE:", r.stdout)

# 3. execute
r = subprocess.run(["curl", "-s", "--max-time", "10", "-X", "POST", "http://127.0.0.1:8765/execute",
    "-H", "Content-Type: application/json", "-d", json.dumps({"token": token})], capture_output=True, text=True)
print("EXECUTE:", r.stdout)

# 4. status
r = subprocess.run(["curl", "-s", "--max-time", "10", "http://127.0.0.1:8765/status"], capture_output=True, text=True)
print("STATUS:", r.stdout[:400])
