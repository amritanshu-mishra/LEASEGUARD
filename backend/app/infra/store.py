from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import db, jdump, jload, rows_to_dicts, row_to_dict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return str(uuid.uuid4())


def log_event(user_id: str, action: str, entity: str, entity_id: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_log(id,user_id,action,entity,entity_id,created_at) VALUES (?,?,?,?,?,?)",
            (uid(), user_id, action, entity, entity_id, now_iso()),
        )


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
    with db() as conn:
        leases = rows_to_dicts(conn.execute("SELECT * FROM leases WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall())
    for l in leases:
        l["documents_count"] = count_documents(l["id"])
        l["invoices_count"] = count_invoices(l["id"])
        l["pending_findings"] = count_pending_findings(l["id"])
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


def delete_lease(lease_id: str, user_id: str) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM leases WHERE id=? AND user_id=?", (lease_id, user_id))
        return cur.rowcount > 0


def count_documents(lease_id: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) c FROM documents WHERE lease_id=?", (lease_id,)).fetchone()
        return int(r["c"]) if r else 0


def count_invoices(lease_id: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) c FROM invoices WHERE lease_id=?", (lease_id,)).fetchone()
        return int(r["c"]) if r else 0


def count_pending_findings(lease_id: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT COUNT(*) c FROM findings WHERE lease_id=? AND approved IS NULL", (lease_id,)).fetchone()
        return int(r["c"]) if r else 0


# --- documents ---

def create_document(lease_id: str, kind: str, name: str, storage_path: str, effective_date: str, sha256: str, page_count: int, text_content: str) -> dict:
    doc_id = uid()
    with db() as conn:
        conn.execute(
            "INSERT INTO documents(id,lease_id,kind,name,storage_path,effective_date,sha256,page_count,text_content) VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, lease_id, kind, name, storage_path, effective_date, sha256, page_count, text_content),
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


def get_invoice(invoice_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
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
                """INSERT INTO findings(id,invoice_id,lease_id,check_type,status,billed_paise,expected_paise,difference_paise,reason,message,controlling_term_json,calculation,approved,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f["id"], invoice_id, lease_id, f["check_type"], f["status"], f["billed_paise"],
                    f.get("expected_paise"), f.get("difference_paise"), f.get("reason"), f["message"],
                    jdump(f.get("controlling_term")) if f.get("controlling_term") else None,
                    f.get("calculation", ""), f.get("approved"), now_iso(),
                ),
            )


def list_findings(invoice_id: str) -> list[dict]:
    with db() as conn:
        rows = rows_to_dicts(conn.execute("SELECT * FROM findings WHERE invoice_id=? ORDER BY created_at", (invoice_id,)).fetchall())
    for r in rows:
        r["controlling_term"] = jload(r.pop("controlling_term_json", None), None)
        if r["approved"] is not None:
            r["approved"] = bool(r["approved"])
    return rows


def get_finding(finding_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["controlling_term"] = jload(r.pop("controlling_term_json", None), None)
        if r["approved"] is not None:
            r["approved"] = bool(r["approved"])
        return r


def set_finding_decision(finding_id: str, user_id: str, approved: bool) -> dict | None:
    with db() as conn:
        conn.execute("UPDATE findings SET approved=? WHERE id=?", (1 if approved else 0, finding_id))
        conn.execute(
            "INSERT INTO approvals(id,finding_id,user_id,decision,decided_at) VALUES (?,?,?,?,?)",
            (uid(), finding_id, user_id, "approved" if approved else "rejected", now_iso()),
        )
    return get_finding(finding_id)
