-- Lead intake pipeline schema (independent demo, sample data only)
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT,
  company TEXT,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  first_received_at TEXT NOT NULL,
  last_received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT,               -- created | duplicate | rejected | error
  error_detail TEXT,
  payload_snapshot TEXT
);
