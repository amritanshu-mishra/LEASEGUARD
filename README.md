# LeaseGuard

**Check your lease charges before you pay.**

LeaseGuard is a lease-charge audit tool for small Indian tenants — shopkeepers, tailors,
chemists, salon owners — who rent a commercial unit and receive a monthly rent +
maintenance invoice. It compares the invoice against the registered lease **and its
amendments**, and shows the exact clause, document, page, effective date and arithmetic
behind every discrepancy. It never claims money was recovered, and it never sends
anything without the tenant's explicit approval.

Built for the **Build for Bharat** hackathon · **Track 4: MSME Growth and Productivity**.

---

## Why this matters

A shopkeeper in Hazratganj gets an invoice: rent ₹44,000, maintenance ₹5,200. The lease
says ₹40,000 with 10% escalation, and maintenance capped at ₹4,000. Amendment No. 1 (signed
in January) capped escalation at 5%. The correct rent is ₹42,000. Nobody in that shop has
the time — or the confidence — to reconstruct anniversary maths, find which document
controls, and quote the right clause in a message to the landlord.

Legal help costs more than the disputed amount. So tenants pay, and overpay again next
month. LeaseGuard closes that gap: upload the lease, the amendment and the invoice; get a
clause-backed list of what looks wrong, decide yourself what to approve, then forward a
ready letter on WhatsApp.

**What LeaseGuard does not do:** it does not give legal advice, does not call anything
fraud, and does not claim recovery. Findings are *discrepancies for review*. Two
independent safeguards keep it honest:

1. Every value must carry a **verbatim quote** from the source document, or the item is
   marked **uncertain** instead of guessed.
2. A dispute letter can only be generated from findings the tenant **explicitly approved**.

---

## Try it in 60 seconds

```bash
# 1. Backend
cd backend
python3 -m pip install -r requirements.txt      # add --break-system-packages if needed
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Frontend (separate terminal, optional for local dev)
cd frontend
npm install
npm run dev        # http://localhost:5173  (proxies /api to :8000)
```

Open <http://localhost:8000> (built SPA is served by FastAPI) or
<http://localhost:5173> (Vite dev server).

Then press **Load Sample Case** → **Audit Invoice** → **Approve** → **Generate Letter** →
**Share on WhatsApp**.

To build the SPA into the backend: `cd frontend && npm run build` (output `frontend/dist`).

---

## What the demo shows

The sample case is **fully synthetic** — fictional parties, fictional property, fictional
amounts. It is generated as *real multi-page PDFs* (`scripts/make_samples.py`) and parsed
back through the same pipeline as user uploads. That is deliberate: the UI cites "page 2"
and "page 4", and anyone can open the PDF and verify the quote really is on that page.

| Document | Pages | Key content |
|---|---|---|
| `Lease_Agreement_Shop12_SYNTHETIC.pdf` | 4 | Rent ₹40,000 (p.2), escalation 10%/yr (p.3), maintenance cap ₹4,000 (p.4) |
| `Amendment_1_Shop12_SYNTHETIC.pdf` | 1 | Effective 2026-01-01, escalation capped at 5% |
| `Invoice_April_2026_SYNTHETIC.pdf` | 1 | Rent ₹44,000, CAM ₹5,200, GST ₹0 |

Expected audit result for April 2026:

| Check | Billed | Expected | Difference | Controlling clause |
|---|---|---|---|---|
| Rent escalation | ₹44,000.00 | ₹42,000.00 | **₹2,000.00** | Amendment 1, p.1, effective 2026-01-01 |
| Maintenance cap | ₹5,200.00 | ₹4,000.00 | **₹1,200.00** | Lease, p.4, effective 2025-04-01 |

Impact summary: **₹3,200.00 excess detected**, **₹3,200.00 flagged for review**, estimated
**~26 min** saved. No claim of money recovered.

**Synthetic data sourcing note.** All names, amounts and addresses are fictional. Clause
patterns mirror structures that are common in Indian commercial leases (fixed monthly
rent, annual percentage escalation, a maintenance cap, amendments that override specific
clauses). Public context for tenancy formalisation: *Uttar Pradesh Regulation of Urban
Premises Tenancy Act, 2021*. No real tenant, landlord or property data is used anywhere in
this repository.

---

## The five requirements this build was asked to meet

| Requirement | Where it lives |
|---|---|
| **Groq instead of Anthropic** | `backend/app/infra/extraction.py` — OpenAI-compatible Groq chat completions (`GROQ_API_KEY`, `llama-3.3-70b-versatile`), with a deterministic fallback |
| **WhatsApp share, not PDF download** | `frontend/src/pages/LetterPage.tsx` — `wa.me/?text=…` with the letter pre-filled, plus copy-to-clipboard. The tenant presses send; nothing is automated |
| **Hindi on key screens** | `frontend/src/i18n.ts` — ~110 EN/HI strings; EN/हिंदी toggle in the header, 9+ Hindi buttons, and a **Hindi dispute letter** for forwarding |
| **Evidence UI** | Every finding shows document name, page, effective date, verbatim clause quote and the full calculation string (`AuditPage.tsx`) |
| **Impact metric** | `backend/app/services/audit_service.py#compute_impact` — excess detected, amount flagged, time saved, explicit "does not claim money recovered". Informational rows are excluded from every total (BR8) |

A rule-by-rule conformance table, including the two bugs this audit found and fixed, is in
[`docs/SPEC_CONFORMANCE.md`](docs/SPEC_CONFORMANCE.md).

---

## How it works

```
Upload (PDF/text)                 Extract                    Audit                   Decide
┌────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐   ┌─────────────────┐
│ lease              │   │ Groq (if key set)    │   │ controlling term     │   │ approve / reject│
│ amendment          │──▶│   ↓ quote validation │──▶│   (precedence rules) │──▶│ per finding     │
│ invoice            │   │ rule-based fallback  │   │ rent escalation      │   │                 │
└────────────────────┘   │   ↓ verbatim quotes  │   │ maintenance cap      │   │ letter (EN/HI)  │
                         └──────────────────────┘   └──────────────────────┘   │ → WhatsApp      │
                                                                               └─────────────────┘
```

### Controlling-term precedence (deterministic)

For a billed period, the controlling value for each term is the one from a **validated,
non-ambiguous** term whose `effective_date <= period_start`, choosing the **latest**
effective date. A same-date tie between two different values is reported as a
**conflict** rather than silently resolved (`backend/app/domain/precedence.py`).

This is what makes the demo interesting: the lease says 10% escalation, the amendment says
5%, and because the amendment took effect *before* the April 2026 anniversary, 5% controls.
The superseded 10% term is still shown in the UI, struck through, for transparency.

### Money and arithmetic

All money is **integer paise**; percentages are **basis points** (5% = 500 bp). Rounding is
half-up at the paise boundary, applied once per figure. No floats touch a rupee amount
(`backend/app/domain/money.py`). Calculations are emitted as human-readable strings so the
tenant — and the landlord — can check each step.

### Honesty rules in extraction

- Groq output is **rejected item by item** if the returned `source_quote` is not a literal
  substring of the document text.
- If no model key is configured, extraction falls back to rule-based parsing that still
  only reports amounts it can quote (`backend/app/infra/heuristics.py`).
- Scanned/image documents are **refused with a clear message** rather than silently
  producing garbage.
- **Fixture values are never used for user uploads.** Fixtures belong only to the labelled
  synthetic sample, so a live upload can never inherit demo numbers.

---

## API

Base path `/api`. JSON in, JSON out.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/leases` | List leases with doc/invoice counts |
| `POST` | `/leases` | Create a lease (`property_label`, optional dates) |
| `POST` | `/leases/sample` | Load the synthetic sample case |
| `POST` | `/leases/{id}/documents` | Multipart upload: `kind=lease\|amendment\|invoice` |
| `GET` | `/leases/{id}?as_of=YYYY-MM-DD` | Lease, documents, terms in force, superseded terms |
| `POST` | `/leases/{id}/audit?invoice_id=…` | Run checks, persist findings |
| `GET` | `/invoices/{id}/findings` | Findings + impact metric |
| `PATCH` | `/findings/{id}` | `{"approved": true\|false}` |
| `POST` | `/invoices/{id}/letter?language=en\|hi` | Letter from approved findings only (`409` if none approved) |
| `DELETE` | `/leases/{id}` | Delete a lease |

---

## Tests

```bash
cd backend
PYTHONPATH=. pytest -q                                    # 48 tests
PYTHONPATH=. pytest -q --cov=app.domain --cov=app.services --cov-fail-under=90
```

**48 tests, 94% branch coverage** on the domain + service layers (the spec target is 90%).
CI runs the same gate on every push (`.github/workflows/ci.yml`).

- `tests/domain/test_golden_cases.py` — the spec's golden cases **G1–G6** and unit cases
  **U1–U2**, plus BR1/BR2/BR7/BR8 rules, written as the spec states them.
- `tests/domain/test_services.py` — the honesty rules: impact metric never inflates or
  claims recovery; letters refuse unapproved findings, never include informational rows,
  always carry the five evidence fields, and keep clause quotes untranslated in Hindi.
- `tests/domain/test_core.py` — money rounding/formatting, precedence, escalation and
  maintenance-cap logic.
- `tests/api/test_flow.py` — upload → audit → approve → letter over HTTP, clean-invoice and
  undercharge handling, duplicate/unreadable upload rejection, letter blocked before
  approval, audit-log contents, cross-user isolation, and a check that the cited PDF page
  numbers really contain the quoted clauses.

---

## Project layout

```
backend/app/
  api/routes.py            HTTP endpoints
  core/config.py           env configuration
  domain/
    money.py               integer-paise arithmetic, INR formatting
    precedence.py          controlling-term resolution
    checks/escalation.py   rent escalation check
    checks/maintenance_cap.py
  infra/
    documents.py           PDF/text → page-tagged text
    extraction.py          Groq + fallback, quote validation
    heuristics.py          deterministic parsers
    db.py, store.py        SQLite persistence
  services/
    audit_service.py       orchestration + impact metric
    letter_service.py      EN/HI dispute letter
    sample_service.py      synthetic sample case
frontend/src/
  pages/                   Dashboard, LeaseDetail, AuditPage, LetterPage, NewLeasePage
  i18n.ts                  EN/HI strings
scripts/make_samples.py    generates the synthetic PDFs
samples/                   the three synthetic PDFs
```

---

## Submission documents

- [`docs/PROBLEM_BRIEF.md`](docs/PROBLEM_BRIEF.md) — problem, users, failure modes, success criteria
- [`docs/VALUE_AND_ADOPTION.md`](docs/VALUE_AND_ADOPTION.md) — value, impact metric definitions, adoption plan, risks, judged-criteria map
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — shot-by-shot demo/video plan
- [`docs/SPEC_CONFORMANCE.md`](docs/SPEC_CONFORMANCE.md) — every business rule, golden case and
  non-functional target mapped to the code, including deliberate departures and known gaps

---

## Deliberate limits

- **Not legal advice.** The UI says so on every screen that matters.
- **No recovery claims.** Metrics are "excess detected", "flagged for review", "time saved".
- **No automated sending.** WhatsApp opens with text pre-filled; the tenant decides.
- **Text-based documents only.** Scans are refused with a message rather than guessed at.
- **Single demo user.** Authentication and multi-tenancy are out of scope for the hackathon;
  the storage layer is user-scoped so this is a schema change, not a rewrite.
- **Two checks implemented.** Rent escalation and maintenance cap. The precedence engine and
  evidence model generalise to deposits, late fees and GST, which is the next step.
- **No authentication.** One demo user. Finding lookups are lease-scoped, so the data layer
  is ready for a `user_id` claim, but there is no login. Judges should read this as a scoped
  demo, not a shipped multi-tenant product.

---

## Demo script (2 minutes)

1. **Dashboard** — point at the synthetic banner and the Hindi toggle.
2. **Load Sample Case** — show the lease, amendment and invoice with effective dates, and the
   struck-through superseded 10% escalation term.
3. **Audit Invoice** — read out the impact metric, then walk one finding top to bottom:
   billed vs expected, document, page, effective date, verbatim clause quote, calculation.
4. **Approve** one finding, **reject** another — show that the letter button counts only
   approvals.
5. **Generate Letter** — switch the letter to **Hindi**, press **Share on WhatsApp**, show the
   pre-filled message. Note that nothing was sent automatically.
6. **New Lease** — upload `samples/upload-demo/*.txt` to show the deterministic extractor
   producing quoted terms with no Groq key configured.

---

## License

Hackathon submission. Synthetic data only; do not upload real tenant documents to the
public demo.

---

## Synthetic sample files

| Path | Purpose |
|---|---|
| `samples/*.pdf` | The demo case as real multi-page PDFs (page numbers cited by the UI) |
| `samples/upload-demo/*.txt` | A second synthetic case for demonstrating the upload path without a Groq key |
| `scripts/make_samples.py` | Regenerates the PDFs — `python3 scripts/make_samples.py` |

Everything under `samples/` is fictional. No real tenant, landlord or property data is used.
