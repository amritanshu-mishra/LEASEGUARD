# Demo Script — LeaseGuard (4 minutes)

Target: a judge who has 3 minutes and 40 seconds of patience. Goal: they should remember
**one** thing — *every discrepancy comes with the clause, the page and the arithmetic.*

---

## Before you record

```bash
# terminal 1
cd backend && PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# terminal 2 (once, to build the SPA the backend serves)
cd frontend && npm install && npm run build
```

1. Open <http://localhost:8000> in a **1280×800** window (looks good in a screen recording).
2. Delete any leftover demo leases so the dashboard starts from the empty state:
   `rm -f backend/leaseguard.db` and restart the server.
3. Set the language to **English** for the first half; switch to **हिंदी** halfway through.
4. Have the three sample PDFs open in a second window (`samples/`) so you can *prove* page 4.

---

## Shot list

### 0:00–0:20 — The problem (talking head or text card)

> "A shopkeeper in Lucknow pays ₹44,000 rent and ₹5,200 maintenance. His lease says ₹40,000
> with 10% escalation, and maintenance capped at ₹4,000. Six months ago he signed an
> amendment capping escalation at 5%. His invoice is wrong by ₹3,200 this month — and he
> has no practical way to know."

### 0:20–0:40 — Dashboard + honesty banner

- Point at the yellow **SYNTHETIC DEMO DATA** banner.
- Point at the Hindi toggle: "This is a Bharat product, not an English product with a
  translation bolted on."
- Press **Load Sample Case**.

### 0:40–1:10 — The documents and the precedence

- Show three documents with **effective dates** (lease 2025-04-01, amendment 2026-01-01,
  invoice 2026-04-01).
- Scroll the "Terms in force" table. Say: *"The amendment took effect before the April 2026
  anniversary, so 5% controls — not the lease's 10%."*
- Point at the **struck-through superseded term**. *"We show the clause we overrode. We
  don't hide it."*

### 1:10–1:50 — The audit (the money shot)

Press **Audit Invoice**. Then walk **one** finding, top to bottom, in this exact order:

1. Impact bar: *"₹3,200 excess detected, ₹3,200 flagged for review, ~26 minutes saved. We
   say 'flagged' — never 'recovered'."*
2. Billed vs Expected vs Difference.
3. **Document** — `Amendment_1_Shop12_SYNTHETIC.pdf`
4. **Page** — `Page 1`. *Open the PDF. Show the clause is on page 1.*
5. **Effective date** — `2026-01-01`.
6. **Clause quote** — read it out loud.
7. **Calculation** — `Rs. 40,000 × (10000+500)/10000 = Rs. 42,000. Billed 44,000. Difference 2,000.`
   *"Any landlord can check this arithmetic."*

Then show the second finding: **Maintenance cap, page 4** of the lease. Open the PDF page 4.

### 1:50–2:20 — Approval is a decision, not a default

- Press **Approve** on rent escalation. Point at the letter button counter going to `1`.
- Press **Reject** on maintenance. Counter stays `1`.
- Say: *"Only what the tenant approves goes in the letter. The product does not send
  anything."*

### 2:20–2:50 — Hindi letter + WhatsApp

- Press **Generate Letter**.
- Switch the letter toggle to **हिंदी पत्र**. Show the Hindi body, and point out the clause
  quote is still in the original language: *"We never rewrite the evidence."*
- Press **Share on WhatsApp**. Show the `wa.me` tab opening with the letter pre-filled.
  *"The shopkeeper presses send. That's it."*

### 2:50–3:10 — Real uploads, not just the sample

- Go to **New Lease**.
- Upload `samples/upload-demo/lease_noida_SYNTHETIC.txt`,
  `amendment_noida_SYNTHETIC.txt`, `invoice_june2025_SYNTHETIC.txt`.
- Land on the audit: **₹2,150.00** flagged, with quotes from the *uploaded* documents.
- Say: *"No API key configured — this is the deterministic extractor, and every value is
  still traceable to a quote in the file."*

### 3:10–3:35 — A photo, and a number that is wrong

Two features judges tend to probe, both worth 20 seconds:

- **A photographed lease.** Upload a photo (or `samples/scan-demo/*.pdf`, a PDF that is
  only a picture). It is read, and the screen says **"Read from a scan"** with a warning
  that a machine read a picture and may have skipped a line. Say: *"OCR drops lines without
  telling you — we measured it dropping the escalation clause while still scoring 0.98
  confidence — so we never show it as clean text, and we say so."*
- **Correcting a bad number.** On any term, press **Fix**, type the right value, save. The
  audit re-runs immediately and the term is marked **corrected by you**, not quoted as
  evidence. Say: *"The machine is allowed to be wrong. It is not allowed to pretend it
  wasn't corrected."*

Optionally: open a second private window and show the two browsers do not share data.

### 3:35–3:55 — Close

> "LeaseGuard doesn't recover money and it doesn't give legal advice. It makes a silent,
> repeating loss visible — with evidence a landlord cannot brush aside — and it does it in
> the language and on the app the tenant already uses. That's the whole product."

---

## Alternate: 60-second cut (for a submission form with a limit)

| Time | Action |
|---|---|
| 0:00 | Text card: the ₹3,200 problem |
| 0:08 | Load Sample → point at the amendment's effective date |
| 0:18 | Audit → impact bar |
| 0:26 | Read one evidence block: document, page 1, quote, calculation |
| 0:40 | Approve → Generate Letter → switch to Hindi → Share on WhatsApp |
| 0:52 | Text card: "Synthetic data. No recovery claims. WhatsApp-first. Built for Bharat." |

---

## Things to say explicitly (they are the differentiators)

- "The page number is real — open the PDF."
- "Uncertain findings are marked uncertain; we don't guess."
- "Fixture values are never used for user uploads."
- "We show the superseded clause, struck through."
- "Excess **detected**, not money **recovered**."
- "Nothing is sent without the tenant's approval."

## Things NOT to say

- "We recover your overpaid rent."
- "This proves fraud."
- "AI reads your lease" (without mentioning quote validation — it undersells the engineering)
- Any number that is not on screen.

---

## Failure drills (if the demo misbehaves)

| Problem | Fix |
|---|---|
| Audit button greyed out | No findings approved yet — that is correct behaviour; approve one first |
| 409 on Generate Letter | Same as above; this is a feature, say so |
| Upload rejected as unreadable | The file is a scan; switch to the text files in `samples/upload-demo/` |
| Groq rate limit | Irrelevant for the demo: `USE_FIXTURES=true` in `.env.example` keeps extraction deterministic |
| Port 8000 busy | `--port 8001` and reload; the SPA is served from the same origin |
