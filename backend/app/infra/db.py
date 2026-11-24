import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..core.config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS leases (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  property_label TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  effective_date TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 1,
  text_content TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS terms (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
  term_type TEXT NOT NULL,
  value_integer INTEGER,
  source_quote TEXT NOT NULL DEFAULT '',
  page INTEGER NOT NULL DEFAULT 1,
  confidence REAL NOT NULL DEFAULT 1.0,
  validated INTEGER NOT NULL DEFAULT 1,
  ambiguous INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
  document_id TEXT,
  period_start TEXT NOT NULL,
  period_label TEXT NOT NULL,
  line_items_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
  check_type TEXT NOT NULL,
  status TEXT NOT NULL,
  billed_paise INTEGER NOT NULL,
  expected_paise INTEGER,
  difference_paise INTEGER,
  reason TEXT,
  message TEXT NOT NULL,
  controlling_term_json TEXT,
  calculation TEXT NOT NULL DEFAULT '',
  approved INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_lease ON documents(lease_id);
CREATE INDEX IF NOT EXISTS idx_terms_document ON terms(document_id);
CREATE INDEX IF NOT EXISTS idx_terms_lease ON terms(lease_id);
CREATE INDEX IF NOT EXISTS idx_findings_invoice ON findings(invoice_id);
CREATE INDEX IF NOT EXISTS idx_approvals_finding ON approvals(finding_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def jload(v: str | None, default: Any):
    if not v:
        return default
    return json.loads(v)
