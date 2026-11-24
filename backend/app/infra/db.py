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
  -- document_id is NULL for a value the tenant typed in themselves, because such
  -- a value has no document to point at, and pretending otherwise would be a lie
  -- of exactly the kind this product exists to prevent.
  id TEXT PRIMARY KEY,
  document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
  lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
  term_type TEXT NOT NULL,
  value_integer INTEGER,
  source_quote TEXT NOT NULL DEFAULT '',
  page INTEGER NOT NULL DEFAULT 1,
  confidence REAL NOT NULL DEFAULT 1.0,
  validated INTEGER NOT NULL DEFAULT 1,
  ambiguous INTEGER NOT NULL DEFAULT 0,
  corrected_by_user INTEGER NOT NULL DEFAULT 0,
  corrected_at TEXT NOT NULL DEFAULT '',
  entered_by_user INTEGER NOT NULL DEFAULT 0,
  user_effective_date TEXT NOT NULL DEFAULT ''
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
  information_only INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
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


# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does not alter an
# existing table, so additive migrations are applied explicitly and idempotently here.
_MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, column DDL)
    ("findings", "information_only", "INTEGER NOT NULL DEFAULT 0"),
    ("audit_log", "detail", "TEXT NOT NULL DEFAULT ''"),
    ("terms", "corrected_by_user", "INTEGER NOT NULL DEFAULT 0"),
    ("terms", "corrected_at", "TEXT NOT NULL DEFAULT ''"),
    # A scan or photo was read with OCR. Kept so the lease screen can keep
    # saying "read from a scan" after a reload, not just in the upload response.
    ("documents", "ocr", "INTEGER NOT NULL DEFAULT 0"),
    ("documents", "ocr_confidence", "REAL NOT NULL DEFAULT 1.0"),
    # A value the tenant typed because the document was silent or contradictory.
    # The spec lets a user resolve an uncertain term; until now there was nowhere
    # to put the answer, and no document to attribute it to.
    ("terms", "entered_by_user", "INTEGER NOT NULL DEFAULT 0"),
    # When the tenant said it. Separate from a document's effective date: a
    # choice made today must not be dated to the lease start, or it loses to the
    # very amendments it is meant to settle.
    ("terms", "user_effective_date", "TEXT NOT NULL DEFAULT ''"),
]


# Migrations that cannot be expressed as ADD COLUMN. SQLite has no
# "ALTER COLUMN", so a table that needs a nullable column rebuilt has to be
# copied. Each one runs once and is detected by inspecting the schema.
def _rebuild_terms_for_user_entered(conn: sqlite3.Connection) -> None:
    """Make terms.document_id nullable, so a tenant-entered value can be stored.

    Guarded by inspecting the live schema rather than a version table: this app
    has one database file and no migration framework, and a check that reads the
    actual table cannot drift from it.
    """
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(terms)")}
    if not cols:
        return  # fresh database; SCHEMA already has the right shape
    # row shape is (cid, name, type, notnull, dflt_value, pk)
    if cols["document_id"][3] == 0:
        return  # already nullable

    conn.executescript(
        """
        PRAGMA foreign_keys=OFF;
        BEGIN;
        CREATE TABLE terms_new (
          id TEXT PRIMARY KEY,
          document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
          lease_id TEXT NOT NULL REFERENCES leases(id) ON DELETE CASCADE,
          term_type TEXT NOT NULL,
          value_integer INTEGER,
          source_quote TEXT NOT NULL DEFAULT '',
          page INTEGER NOT NULL DEFAULT 1,
          confidence REAL NOT NULL DEFAULT 1.0,
          validated INTEGER NOT NULL DEFAULT 1,
          ambiguous INTEGER NOT NULL DEFAULT 0,
          corrected_by_user INTEGER NOT NULL DEFAULT 0,
          corrected_at TEXT NOT NULL DEFAULT '',
          entered_by_user INTEGER NOT NULL DEFAULT 0,
          user_effective_date TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO terms_new (id, document_id, lease_id, term_type, value_integer,
                               source_quote, page, confidence, validated, ambiguous,
                               corrected_by_user, corrected_at, entered_by_user,
                               user_effective_date)
        SELECT id, document_id, lease_id, term_type, value_integer,
               source_quote, page, confidence, validated, ambiguous,
               COALESCE(corrected_by_user, 0), COALESCE(corrected_at, ''), 0, ''
        FROM terms;
        DROP TABLE terms;
        ALTER TABLE terms_new RENAME TO terms;
        COMMIT;
        PRAGMA foreign_keys=ON;
        """
    )


_STEP_MIGRATIONS = (_rebuild_terms_for_user_entered,)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table created fresh by SCHEMA, already has the column
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    for migrate in _STEP_MIGRATIONS:
        migrate(conn)


_initialised = False


def init_db(force: bool = False) -> None:
    """Create tables, once.

    Idempotent and cheap after the first call, so every store entry point can
    call it. That matters beyond the server: a bare `TestClient(app)` never
    fires the startup hook, and before this the app answered requests against a
    database with no tables at all - which only looked like it worked because the
    developer's real database happened to exist.
    """
    global _initialised
    if _initialised and not force:
        return
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        # Re-run SCHEMA after a table rebuild: dropping and renaming a table drops
        # its indexes with it, and CREATE INDEX IF NOT EXISTS is idempotent, so
        # this restores them without duplicating anything.
        conn.executescript(SCHEMA)
    _initialised = True


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    # Safety net: a request must never hit a database without tables. Cheap
    # after the first call, and it makes every entry point self-sufficient
    # rather than depending on the startup hook having run.
    init_db()
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
