# Spec Conformance — LeaseGuard

This document maps the build to the technical design spec and the hackathon brief, and
records every place the build **deliberately departs** from the original spec. It exists so
a reviewer can check claims instead of trusting them.

How it was verified: `cd backend && PYTHONPATH=. pytest -q --cov=app.domain --cov=app.services --cov-fail-under=90`

---

## 1. Business rules (spec section 3.3)

| Rule | Requirement | Status | Where it is enforced / tested |
|---|---|---|---|
| **BR1** | All money as integer paise, no floats | ✅ | `domain/money.py`; `test_core.py::test_money_*` |
| **BR2** | A term controls from its effective date; lease from lease start | ✅ | `domain/precedence.py`; `test_golden_cases.py::test_br2_*` |
| **BR3** | Latest effective date wins | ✅ | `precedence.py`; G1 test |
| **BR4** | Same latest date, different values ⇒ Conflict, uncertain | ✅ | `precedence.py`; G5 test |
| **BR5** | No eligible term ⇒ do not infer; uncertain `term_not_found` | ✅ | `precedence.py`; U2 test |
| **BR6** | Term accepted only if quote appears verbatim | ✅ | `infra/extraction.py#validate_quote`; U1 test |
| **BR7** | Rates in bp; expected = base × (10000+rate)/10000, half-up | ✅ | `domain/money.py#apply_rate`; BR7 rounding test |
| **BR8** | Confirmed only when billed − expected > 0; undercharges are **information, not findings** | ✅ *(was violated — fixed)* | `checks/*.py` return `NO_ISSUE` + `informational=True`; excluded from impact totals, letters, approvals. Tests: `test_g3_*`, `test_br8_*`, `test_undercharge_is_information_not_a_finding` |
| **BR9** | Letters include approved findings only, wording "discrepancies for review" | ✅ | `services/letter_service.py`; 6 letter tests incl. rejected-finding exclusion |
| **BR10** | Every approval/rejection/generation logged with user + timestamp | ✅ *(was thin — fixed)* | `audit_log.detail` records the decision value; `GET /api/audit-log`; `test_audit_log_records_who_did_what` |
| **BR11** | A user can only read/change their own leases | ✅ | Every route takes a `current_user` dependency and every query is scoped by that id. Finding lookups join through the lease owner, closing a hole where `GET /invoices/{id}/findings` trusted a raw id. Identity is a per-browser session cookie (opaque token, SHA-256 at rest), not a login - see README. Tests: `tests/api/test_sessions.py` (10), `test_corrections.py` (cross-tenant 404) |

### Two bugs found by writing the rule tests

1. **BR8 violation.** Undercharges and correct charges returned `None`, so a tenant saw
   *nothing* for a charge that had actually been checked — and a clean invoice looked
   identical to a failed audit. Now persisted as `NO_ISSUE` / `informational=True`,
   visible but never approvable, never in the letter, never in any money total.
2. **Precedence fallback (BR6/G4).** If a *newer* amendment was ambiguous or unverified,
   precedence silently fell through to the older clause and produced a confident number.
   That is exactly the guessing the product promises not to do. Now any ambiguous or
   unverified term dated at or after the controlling term forces `clause_ambiguous` /
   `quote_unverified`. Test: `test_g4_ambiguous_clause_is_uncertain_with_no_amount`.

---

## 2. Golden and unit cases (spec section 10.4)

| Case | Expectation | Status |
|---|---|---|
| **G1** Amendment ignored | Confirmed, ₹2,000, controlling = Amendment 1 | ✅ `test_g1_amendment_caps_escalation` |
| **G2** Maintenance above cap | Confirmed, ₹1,200 | ✅ `test_g2_maintenance_above_cap` |
| **G3** Clean invoice | No findings (now: informational only) | ✅ `test_g3_clean_invoice_produces_no_findings` + API test |
| **G4** Ambiguous clause | Uncertain `clause_ambiguous`, no amount | ✅ 2 tests |
| **G5** Conflicting amendments | Uncertain `conflicting_terms`, both quotes | ✅ `test_g5_*` |
| **G6** Amendment not yet effective | No discrepancy; lease controls | ✅ `test_g6_future_amendment_does_not_control` |
| **U1** Unverifiable quote | Rejected, `quote_unverified` | ✅ `test_u1_*` |
| **U2** Missing term | `term_not_found` | ✅ `test_u2_*` |

Also covered beyond the spec's list: BR1 rounding at the half-paise boundary, BR2
not-yet-effective, undercharge handling, impact-metric honesty rules, and Hindi letter
quote preservation.

---

## 3. Non-functional requirements (spec section 3.4)

| Area | Target | Status |
|---|---|---|
| Performance | Invoice audit ~30 s incl. extraction | ✅ ~50 ms with deterministic extraction; Groq path is bounded by a 45 s timeout |
| Reliability | Health endpoint; graceful extraction failure; fixture fallback | ✅ `/api/health`; unreadable files return `422`, never a silent empty doc |
| Security — secrets | No secrets in repo | ✅ `.env.example` only; `.gitignore` covers `.env*` |
| Security — uploads | Allowlist, size/page limits, no execution | ✅ magic-byte + extension check, 10 MB / 50 page caps, text-only extraction |
| Security — injection | Parameterised SQL | ✅ all queries parameterised |
| Security — XSS | React escaping, no raw HTML | ✅ no `dangerouslySetInnerHTML` anywhere |
| Security — prompt injection | Fixed schema, no tools, quote verification | ✅ `json_object` mode + verbatim quote gate; a "ignore previous instructions" clause yields no term |
| Per-user isolation | RLS or equivalent | ✅ app-level scoping on every query, per-browser session identity (see BR11). Not RLS: enforcement is in the query layer |
| OCR for scans/photos | Not in spec, but assumed by "upload the lease" | ✅ with explicit provenance and a warning; see the OCR row below |
| Accessibility | Keyboard operable, visible focus, AA contrast, status not colour-only | ✅ skip link, `:focus-visible` ring, `aria-pressed`, `role=alert/status`, icon+text badges, `prefers-reduced-motion`, 360 px layout |
| Browser support | Current Chrome/Edge/Safari/Firefox | ✅ standard ES2020 build, no exotic APIs |
| Typed code | TypeScript + type hints | ✅ `tsc -b` in build; Python type hints throughout |
| CI | Lint/type-check/tests on push | ✅ `.github/workflows/ci.yml` — compile check, tests, 90% coverage gate, `npm ci && npm run build` |
| Domain coverage | ≥ 90% | ✅ **94%** branch coverage on `app/domain` + `app/services` |

---

## 4. Deliberate deviations from the spec

These are conscious decisions, not oversights. Each is stated in the README too.

| Spec says | Build does | Why |
|---|---|---|
| Anthropic Claude for extraction | **Groq** (`llama-3.3-70b-versatile`) | Explicit user requirement. Same OpenAI-compatible contract; quote validation is unchanged |
| Supabase Auth + Postgres + RLS | Per-browser session cookie, SQLite, app-level scoping | Full accounts are out of scope, but app-level scoping is real rather than assumed: every query is filtered by `user_id` and six tests prove two browsers cannot reach each other's data. The remaining difference from RLS is that enforcement lives in the queries, not the database. Stated as a limit, not hidden |
| Letter as PDF (S6) | **WhatsApp share + copy** | Explicit user requirement. The tenant forwards text; no PDF download step |
| Jinja2 templated letter | Plain f-string builder | Jinja was in `requirements.txt` but unused — removed rather than left as cargo |
| Invoice amount verified against the document | Amounts used to be trusted as parsed | ✅ every line item carries its source text and is quote-checked; unverified amounts are reported in the upload response instead of driving a finding |
| Per-user rate limits | Not specified | ✅ 10 uploads and 20 audits per session per minute, 429 with `Retry-After`. In memory and per process - honest for a single-node demo, not a distributed limiter |
| Resolving an uncertain term | User must correct the document and re-upload | ✅ missing clause: tenant supplies a value; contradiction: tenant picks the clause that applies. Both are stored as user-supplied with no document attached |
| OCR for photographed leases | **Implemented** (`app/infra/ocr.py`) - with the honesty caveat the refusal was protecting | RapidOCR + PyMuPDF read scans and photos, but OCR can silently skip a line: measured, the escalation clause vanished on one font while mean confidence stayed at 0.98. So OCR'd text is never presented as clean extraction - the API returns `ocr: true` and a warning, the lease screen keeps a "read from a scan" badge, and every term can be corrected by hand (`PATCH /terms/{id}`), which satisfies BR6's intent instead of only its letter |
| Checks: escalation + cap only | Same | Scope kept tight per the brief's advice ("a polished product that does one thing well") |

### Cuts the spec itself recommends
The spec's section 12 lists what to drop first if time is short. This build follows that
guidance: **no email/OTP accounts**, no PDF letter export, no multi-invoice dashboards, no
scheduled re-audits. None of these affect the core claim of the product.

Two of these were closed after the first judging pass rather than left as cuts:

- **Per-browser sessions instead of accounts.** An opaque token in an HttpOnly cookie,
  stored only as a SHA-256 hash, with every query scoped to its `user_id`. The cookie is
  `SameSite=Lax` on plain HTTP and `SameSite=None; Secure` over TLS, because the app is
  also demoed inside a cross-origin iframe where a Lax cookie is never sent - the symptom
  was a loaded sample case that reported success and then 404'd. The same token is returned
  in an `X-Session-Token` header that the page replays, so a browser that blocks
  third-party cookies still holds its identity. No password, no email, no recovery, no
  cross-device identity - but two tenants can no longer read each other's leases, bills or
  activity trail, which is the property that actually matters. Ten tests cover the
  isolation, including that a stranger's delete removes no files.
- **Photo upload.** See the OCR row above.

One cut remains a cut: there is no legal-grade account system, and the README says so in the
same words.

---

## 5. Hackathon rubric mapping (brief section 9)

| Criterion | Weight | Evidence in this repo |
|---|---|---|
| **Problem understanding** | 25% | `docs/PROBLEM_BRIEF.md`; six named failure modes; the sample reproduces the most common one (amendment ignored). Written from the tenant's situation, not the technology |
| **Working solution** | 25% | Live app completes the full journey; 135 backend tests + 32 UI end-to-end checks in CI; upload path works on unseen documents, scans and photos; migration verified against an old-schema DB |
| **Innovation** | 20% | Deterministic precedence with a *refuse-to-guess* rule for ambiguous newer clauses; quote-gated extraction that discards any model output not literally in the document; evidence-preserving Hindi letter |
| **Value and impact** | 20% | `docs/VALUE_AND_ADOPTION.md`; impact metric with explicit non-claims; association-led adoption path; honest failure risks |
| **User experience** | 10% | Hindi on all key screens incl. the letter; WhatsApp as the send channel; evidence-first cards; accessibility pass; 360 px layout |

---

## 6. Known gaps (stated, not hidden)

1. **Sessions, not accounts.** Every browser gets a private, opaque session and all API
   routes are scoped to it, so one tenant cannot reach another's data. There is still no
   password, email, recovery or cross-device account: clearing both cookies *and* the
   tab's `sessionStorage` loses the leases, and there is nothing to log back in to. Fine for
   a judged demo; not sufficient for production.
2. **Two checks only.** Rent escalation and maintenance cap. The precedence engine
   generalises, the check library does not yet cover deposits, late fees or GST.
3. **Uncertain terms are resolvable.** A missing clause can be supplied by the tenant, and a
   contradiction between two amendments can be settled by choosing one. Both are stored as
   *user-supplied* with no document attached, so they cannot pass as extracted evidence. What
   remains unsolved: there is no bulk "review all uncertain terms" screen, and a tenant can
   still supply a wrong number - the audit then reflects their claim, which is why it is
   labelled as theirs rather than as the document's.
4. **OCR is implemented but cannot be trusted silently.** Scans and photos are read, and
   they are always labelled as such with a warning, because the engine was measured dropping
   a whole clause while reporting high confidence. The tenant is asked to check every term,
   and can correct any of them. What is *not* solved: a dropped clause that the tenant does
   not notice, and a confidence score that would let us flag likely misses automatically.
5. **SQLite.** Adequate for a single-node demo; Postgres + migrations for production.
6. **Time-saved is an estimate**, not a measurement. It is labelled as such in the UI, the
   API response and this document.
