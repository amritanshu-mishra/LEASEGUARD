from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import db, jdump, jload, rows_to_dicts, row_to_dict

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return str(uuid.uuid4())


def log_event(user_id: str, action: str, entity: str, entity_id: str, detail: str = "") -> None:
    """Append an audit-log event (BR10).

    Every call records who acted, what they did, which entity it touched, and when.
    `detail` carries the decision or other non-secret context (never document text).
    """
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log(id,user_id,action,entity,entity_id,detail,created_at) VALUES (?,?,?,?,?,?,?)",
            (uid(), user_id, action, entity, entity_id, detail, now_iso()),
        )


def list_audit_log(user_id: str, limit: int = 100) -> list[dict]:
    """Read a user's own audit trail, newest first (BR10 + BR11)."""
    with db() as conn:
        rows = rows_to_dicts(conn.execute(
            "SELECT * FROM audit_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall())
    return rows


# --- leases ---

def create_lease(user_id: str, property_label: str, start_date: str, end_date: str) -> dict:
    lease_id = uid()
    with db() as conn:
        conn.execute(
            "INSERT INTO leases(id,user_id,property_label,start_date,end_date,created_at) VALUES (?,?,?,?,?,?)",
            (lease_id, user_id, property_label, start_date, end_date, now_iso()),
        )
    return get_lease(lease_id, user_id)  # type: ignore


def list_leases(user_id: str) -> list[dict]:
    """Leases with their counts, in one query per table instead of per lease.

    The per-lease loop ran three follow-up queries each, so a market association with
    500 leases issued 1501 queries and took ~310 ms just to paint the dashboard.
    """
    with db() as conn:
        leases = rows_to_dicts(conn.execute(
            "SELECT * FROM leases WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall())
        if not leases:
            return leases
        lease_ids = [l["id"] for l in leases]

        docs: dict[str, int] = {}
        invoices: dict[str, int] = {}
        pending: dict[str, int] = {}

        # Chunked to stay well under SQLite's bound-parameter limit on old builds.
        chunk = 400
        for start in range(0, len(lease_ids), chunk):
            part = lease_ids[start:start + chunk]
            marks = ",".join("?" * len(part))
            for row in conn.execute(
                f"SELECT lease_id, COUNT(*) c FROM documents WHERE lease_id IN ({marks}) GROUP BY lease_id",
                part,
            ):
                docs[row["lease_id"]] = int(row["c"])
            for row in conn.execute(
                f"SELECT lease_id, COUNT(*) c FROM invoices WHERE lease_id IN ({marks}) GROUP BY lease_id",
                part,
            ):
                invoices[row["lease_id"]] = int(row["c"])
            for row in conn.execute(
                f"SELECT lease_id, COUNT(*) c FROM findings "
                f"WHERE lease_id IN ({marks}) AND approved IS NULL AND information_only=0 GROUP BY lease_id",
                part,
            ):
                pending[row["lease_id"]] = int(row["c"])

    for lease in leases:
        lease["documents_count"] = docs.get(lease["id"], 0)
        lease["invoices_count"] = invoices.get(lease["id"], 0)
        lease["pending_findings"] = pending.get(lease["id"], 0)
    return leases


def get_lease(lease_id: str, user_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM leases WHERE id=? AND user_id=?", (lease_id, user_id)).fetchone()
        if not row:
            return None
        lease = dict(row)
        lease["documents"] = rows_to_dicts(conn.execute("SELECT * FROM documents WHERE lease_id=? ORDER BY effective_date", (lease_id,)).fetchall())
        lease["invoices"] = rows_to_dicts(conn.execute("SELECT * FROM invoices WHERE lease_id=? ORDER BY period_start DESC", (lease_id,)).fetchall())
        for inv in lease["invoices"]:
            inv["line_items"] = jload(inv.pop("line_items_json", "[]"), [])
    return lease


def document_storage_paths(lease_id: str) -> list[str]:
    """Storage paths belonging to a lease, read *before* the rows are deleted."""
    with db() as conn:
        rows = conn.execute(
            "SELECT storage_path FROM documents WHERE lease_id=?", (lease_id,)
        ).fetchall()
    return [r["storage_path"] for r in rows if r["storage_path"]]


def storage_path_still_referenced(storage_path: str) -> bool:
    """True if any surviving document row still points at this file.

    File names are content-addressed (sha prefix + filename), so the same document
    uploaded to two leases shares one file. Unlinking it while the other lease still
    exists would destroy that lease's document.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE storage_path=? LIMIT 1", (storage_path,)
        ).fetchone()
    return row is not None


def delete_lease(lease_id: str, user_id: str) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM leases WHERE id=? AND user_id=?", (lease_id, user_id))
        return cur.rowcount > 0


def purge_orphan_files(paths: list[str]) -> int:
    """Delete uploaded files that no document row references any more.

    Deleting a lease removes its rows (ON DELETE CASCADE) but the raw file used to
    stay on disk forever, leaving readable rent figures behind after the user asked
    for the lease to be gone. This closes that.
    """
    removed = 0
    for path in paths:
        if storage_path_still_referenced(path):
            continue
        try:
            fp = Path(path)
            if fp.is_file():
                fp.unlink()
                removed += 1
        except OSError:
            # A file we cannot remove must not fail the delete the user asked for.
            log.warning("could not remove uploaded file %s", path, exc_info=True)
    return removed


def count_documents(lease_id: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) c FROM documents WHERE lease_id=?", (lease_id,)).fetchone()
        return int(r["c"]) if r else 0


def count_invoices(lease_id: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) c FROM invoices WHERE lease_id=?", (lease_id,)).fetchone()
        return int(r["c"]) if r else 0


def count_pending_findings(lease_id: str) -> int:
    """Undecided *discrepancy* findings (BR8: informational rows never need a decision)."""
    with db() as conn:
        r = conn.execute(
            "SELECT COUNT(*) c FROM findings "
            "WHERE lease_id=? AND approved IS NULL AND information_only=0",
            (lease_id,),
        ).fetchone()
        return int(r["c"]) if r else 0


# --- documents ---

def create_document(lease_id: str, kind: str, name: str, storage_path: str, effective_date: str, sha256: str, page_count: int, text_content: str, ocr: bool = False, ocr_confidence: float = 1.0) -> dict:
    doc_id = uid()
    with db() as conn:
        conn.execute(
            "INSERT INTO documents(id,lease_id,kind,name,storage_path,effective_date,sha256,page_count,text_content,ocr,ocr_confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, lease_id, kind, name, storage_path, effective_date, sha256, page_count, text_content,
             int(bool(ocr)), float(ocr_confidence)),
        )
    return get_document(doc_id)  # type: ignore


def get_document(doc_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return row_to_dict(row)


def list_documents(lease_id: str) -> list[dict]:
    with db() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM documents WHERE lease_id=? ORDER BY effective_date", (lease_id,)).fetchall())


# --- terms ---

def replace_terms_for_document(document_id: str, lease_id: str, terms: list[dict]) -> None:
    with db() as conn:
        conn.execute("DELETE FROM terms WHERE document_id=?", (document_id,))
        for t in terms:
            conn.execute(
                """INSERT INTO terms(id,document_id,lease_id,term_type,value_integer,source_quote,page,confidence,validated,ambiguous)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    t.get("id") or uid(), document_id, lease_id, t["term_type"], t.get("value"),
                    t.get("source_quote", ""), int(t.get("page", 1)), float(t.get("confidence", 1.0)),
                    1 if t.get("validated", True) else 0, 1 if t.get("ambiguous", False) else 0,
                ),
            )


def list_terms(lease_id: str) -> list[dict]:
    with db() as conn:
        rows = rows_to_dicts(conn.execute(
            """SELECT t.*, d.name as document_name, d.kind as document_kind, d.effective_date, d.text_content as document_text
               FROM terms t JOIN documents d ON d.id=t.document_id WHERE t.lease_id=? ORDER BY d.effective_date""",
            (lease_id,),
        ).fetchall())
    for r in rows:
        r["validated"] = bool(r["validated"])
        r["ambiguous"] = bool(r["ambiguous"])
        r["corrected_by_user"] = bool(r.get("corrected_by_user"))
    return rows


# --- invoices ---

def create_invoice(lease_id: str, document_id: str | None, period_start: str, period_label: str, line_items: list[dict]) -> dict:
    inv_id = uid()
    with db() as conn:
        conn.execute(
            "INSERT INTO invoices(id,lease_id,document_id,period_start,period_label,line_items_json) VALUES (?,?,?,?,?,?)",
            (inv_id, lease_id, document_id, period_start, period_label, jdump(line_items)),
        )
    return get_invoice(inv_id)  # type: ignore


def get_invoice(invoice_id: str, user_id: str | None = None) -> dict | None:
    """Fetch an invoice. Pass user_id to enforce ownership.

    Callers that read an invoice must pass user_id: invoice ids are opaque but not
    secret, and without the join any authenticated browser could read another
    tenant's bill by guessing or observing an id.
    """
    with db() as conn:
        if user_id is None:
            row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT i.* FROM invoices i JOIN leases l ON l.id=i.lease_id "
                "WHERE i.id=? AND l.user_id=?",
                (invoice_id, user_id),
            ).fetchone()
        if not row:
            return None
        inv = dict(row)
        inv["line_items"] = jload(inv.pop("line_items_json", "[]"), [])
        return inv


def list_invoices(lease_id: str) -> list[dict]:
    with db() as conn:
        rows = rows_to_dicts(conn.execute("SELECT * FROM invoices WHERE lease_id=? ORDER BY period_start DESC", (lease_id,)).fetchall())
    for r in rows:
        r["line_items"] = jload(r.pop("line_items_json", "[]"), [])
    return rows


# --- findings ---

def replace_findings_for_invoice(invoice_id: str, lease_id: str, findings: list[dict]) -> None:
    with db() as conn:
        # keep decided findings? Spec says "re-running an audit for the same invoice replaces the earlier findings that have no decisions"
        conn.execute("DELETE FROM findings WHERE invoice_id=? AND approved IS NULL", (invoice_id,))
        for f in findings:
            conn.execute(
                """INSERT INTO findings(id,invoice_id,lease_id,check_type,status,billed_paise,expected_paise,difference_paise,reason,message,controlling_term_json,calculation,approved,information_only,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f["id"], invoice_id, lease_id, f["check_type"], f["status"], f["billed_paise"],
                    f.get("expected_paise"), f.get("difference_paise"), f.get("reason"), f["message"],
                    jdump(f.get("controlling_term")) if f.get("controlling_term") else None,
                    f.get("calculation", ""), f.get("approved"),
                    1 if f.get("informational") else 0, now_iso(),
                ),
            )


def _finding_row_to_dict(r: dict) -> dict:
    """Normalise a findings row: JSON column, SQLite ints -> bools, legacy column default."""
    out = dict(r)
    out["controlling_term"] = jload(out.pop("controlling_term_json", None), None)
    out["approved"] = None if out.get("approved") is None else bool(out["approved"])
    out["informational"] = bool(out.pop("information_only", 0))
    return out


def list_findings(invoice_id: str) -> list[dict]:
    with db() as conn:
        rows = rows_to_dicts(conn.execute(
            "SELECT * FROM findings WHERE invoice_id=? ORDER BY information_only, created_at",
            (invoice_id,),
        ).fetchall())
    return [_finding_row_to_dict(r) for r in rows]


def get_finding_for_user(finding_id: str, user_id: str) -> dict | None:
    """Fetch a finding only if its lease belongs to user_id (BR11).

    A finding is scoped by invoice -> lease -> user, so the join enforces ownership
    rather than trusting the caller to have checked.
    """
    with db() as conn:
        row = conn.execute(
            """SELECT f.* FROM findings f
               JOIN leases l ON l.id = f.lease_id
               WHERE f.id=? AND l.user_id=?""",
            (finding_id, user_id),
        ).fetchone()
    if not row:
        return None
    return _finding_row_to_dict(dict(row))


def get_finding(finding_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        if not row:
            return None
        return _finding_row_to_dict(dict(row))


def set_finding_decision(finding_id: str, user_id: str, approved: bool) -> dict | None:
    with db() as conn:
        conn.execute("UPDATE findings SET approved=? WHERE id=?", (1 if approved else 0, finding_id))
        conn.execute(
            "INSERT INTO approvals(id,finding_id,user_id,decision,decided_at) VALUES (?,?,?,?,?)",
            (uid(), finding_id, user_id, "approved" if approved else "rejected", now_iso()),
        )
    return get_finding(finding_id)


# --- sessions ----------------------------------------------------------------

def create_session(token_hash: str, user_id: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions(token_hash,user_id,created_at) VALUES (?,?,?)",
            (token_hash, user_id, now_iso()),
        )


def user_for_session_hash(token_hash: str) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()
    return row["user_id"] if row else None


# --- manual term corrections -------------------------------------------------

def get_term_for_user(term_id: str, user_id: str) -> dict | None:
    """A term, only if it belongs to a lease this user owns."""
    with db() as conn:
        row = conn.execute(
            """SELECT t.*, d.name AS document_name
               FROM terms t
               JOIN leases l ON l.id = t.lease_id
               JOIN documents d ON d.id = t.document_id
               WHERE t.id=? AND l.user_id=?""",
            (term_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def correct_term(term_id: str, user_id: str, value: int | None, note: str = "") -> dict | None:
    """Apply a tenant's correction to an extracted term.

    Extraction is heuristic, so a wrong number is always possible: a "4" read as
    "40" changes the whole audit. Without a correction path the only remedy was to
    delete the lease and start over. A correction is marked so the UI never presents
    a hand-entered value as machine-extracted evidence.
    """
    if not get_term_for_user(term_id, user_id):
        return None
    with db() as conn:
        conn.execute(
            """UPDATE terms
               SET value_integer=?, ambiguous=0, validated=1,
                   confidence=1.0, corrected_by_user=1, corrected_at=?
               WHERE id=?""",
            (value, now_iso(), term_id),
        )
    store_note = note.strip()[:300]
    if store_note:
        with db() as conn:
            conn.execute("UPDATE terms SET source_quote=? WHERE id=?", (store_note, term_id))
    updated = get_term_for_user(term_id, user_id)
    if updated:
        updated["corrected_by_user"] = bool(updated.get("corrected_by_user"))
        updated["validated"] = bool(updated.get("validated"))
        updated["ambiguous"] = bool(updated.get("ambiguous"))
    return updated
