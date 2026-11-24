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
| **BR11** | A user can only read/change their own leases | ⚠️ **partial, deliberate** | Finding lookups are lease-scoped (`get_finding_for_user`); leases are user-scoped. **No authentication** — single demo user, stated in README. Tests: `test_finding_from_another_user_is_not_visible` |

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
| Per-user isolation | RLS or equivalent | ⚠️ app-level scoping only (see BR11) |
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
| Supabase Auth + Postgres + RLS | Single demo user, SQLite, app-level scoping | Auth is out of scope for a 2-day demo; the repository layer is already user-scoped, so adding a `user_id` claim is contained work. Stated as a limit, not hidden |
| Letter as PDF (S6) | **WhatsApp share + copy** | Explicit user requirement. The tenant forwards text; no PDF download step |
| Jinja2 templated letter | Plain f-string builder | Jinja was in `requirements.txt` but unused — removed rather than left as cargo |
| OCR for photographed leases | Refused with a clear message | Refusing is honest; guessing from a skewed photo would violate BR6. OCR + human confirmation is on the roadmap |
| Checks: escalation + cap only | Same | Scope kept tight per the brief's advice ("a polished product that does one thing well") |

### Cuts the spec itself recommends
The spec's section 12 lists what to drop first if time is short. This build follows that
guidance: no email/OTP auth, no PDF letter export, no multi-invoice dashboards, no
photo upload, no scheduled re-audits. None of these affect the core claim of the product.

---

## 5. Hackathon rubric mapping (brief section 9)

| Criterion | Weight | Evidence in this repo |
|---|---|---|
| **Problem understanding** | 25% | `docs/PROBLEM_BRIEF.md`; six named failure modes; the sample reproduces the most common one (amendment ignored). Written from the tenant's situation, not the technology |
| **Working solution** | 25% | Live app completes the full journey; 48 tests; upload path works on unseen documents; migration verified against an old-schema DB |
| **Innovation** | 20% | Deterministic precedence with a *refuse-to-guess* rule for ambiguous newer clauses; quote-gated extraction that discards any model output not literally in the document; evidence-preserving Hindi letter |
| **Value and impact** | 20% | `docs/VALUE_AND_ADOPTION.md`; impact metric with explicit non-claims; association-led adoption path; honest failure risks |
| **User experience** | 10% | Hindi on all key screens incl. the letter; WhatsApp as the send channel; evidence-first cards; accessibility pass; 360 px layout |

---

## 6. Known gaps (stated, not hidden)

1. **No authentication.** One demo user. A finding can only be fetched through its lease
   owner, but there is no login. Fine for a judged demo; not fine for production.
2. **Two checks only.** Rent escalation and maintenance cap. The precedence engine
   generalises, the check library does not yet cover deposits, late fees or GST.
3. **Text-based documents only.** Scans are refused. This is the single biggest real-world
   adoption blocker and the next engineering task.
4. **SQLite.** Adequate for a single-node demo; Postgres + migrations for production.
5. **Time-saved is an estimate**, not a measurement. It is labelled as such in the UI, the
   API response and this document.
