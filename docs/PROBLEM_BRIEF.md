# Problem Brief — LeaseGuard

**Hackathon:** Build for Bharat · **Track:** 4 — MSME Growth and Productivity
**Team:** LeaseGuard · **Repo:** this repository

---

## The problem, in one paragraph

A small shopkeeper signs a lease with a monthly rent, an annual escalation clause, and a
maintenance charge. Over the following years they sign an amendment or two, and every month
they receive an invoice. The invoice is prepared by the landlord or the property manager,
and the tenant has neither the time nor the confidence to check it. Anniversary arithmetic
is easy to get wrong on purpose or by accident. Amendments are easy to forget. And when a
tenant does suspect an overcharge, the amount at stake — a few hundred to a few thousand
rupees — is far below the cost of a lawyer or even a CA's letter. So the tenant pays. Month
after month.

This is a **silent, repeating, small-value loss** spread across crores of MSME tenants. It
does not show up in any single large dispute because nobody disputes it.

---

## Who has this problem

- **Commercial shop tenants** — kirana, stationery, tailoring, pharmacy, salons, small
  eateries — typically 100–500 sq ft, rent ₹15,000–₹80,000/month.
- **Small office tenants** — freelancers, clinics, coaching centres, small agencies.
- **Micro-landlords' tenants in mixed-use markets** where a committee or manager issues a
  consolidated invoice with CAM charges.

They share three traits that shape the solution:

1. **Mobile-first.** The lease is a PDF in WhatsApp or a photo in the gallery, not a file on
   a laptop. The dispute is settled in a WhatsApp conversation, not by registered post.
2. **Low legal literacy, high social cost of asking.** The tenant must keep the
   relationship. Confrontation needs *specific*, *quotable* evidence — not a general
   suspicion.
3. **No time.** A shop opens at 9 and closes at 9. Six hours of lease reading is not
   happening.

---

## What goes wrong in practice

| Failure | Real shape | Why it is hard to catch |
|---|---|---|
| Escalation applied above the clause | 10% applied where the amendment caps 5% | The lease and amendment are separate documents |
| Escalation applied on the wrong base | % applied to last year's invoiced rent rather than the contracted base | Compounding hides in plain sight |
| Escalation applied on a non-anniversary month | Increase in July instead of the April anniversary | Invoice looks normal |
| Maintenance above the cap | ₹5,200 billed against a ₹4,000 cap | Cap clause is buried on page 4 |
| Amendment not yet effective | New rate applied four months early | Effective dates are easy to skip |
| Two documents disagree | Both current, different values | Requires a precedence rule, not a guess |

Every one of these is **mechanically checkable** given the documents. None of them requires
a court to determine *whether* the arithmetic is wrong — only whether the documents say what
they say.

---

## Why existing tools don't solve it

- **Lawyers / legal aid:** economically irrational for ₹2,000/month, and unavailable at the
  moment of doubt (the tenant opens the invoice at 9 pm).
- **Spreadsheets:** require the tenant to first understand the clauses — the exact skill
  gap the problem is about.
- **Generic AI chat:** will happily *invent* a clause, cannot cite a page, and produces text
  that a landlord (rightly) will not accept as evidence.
- **Property-management apps:** built for landlords, not tenants, and not for Indian
  paper-lease workflows.

The missing piece is not intelligence — it is **grounded, citable arithmetic plus a
respectful way to raise it**.

---

## What a good solution must do

1. **Read the actual documents** — lease, amendment(s), invoice — and nothing else.
2. **Establish which clause controls** for the billed period, deterministically, using
   effective dates. Show the superseded clause too.
3. **Show the evidence for every single discrepancy**: document, page, effective date,
   verbatim quote, and the arithmetic step by step. If evidence is missing, say
   **"uncertain"** — never guess.
4. **Keep the tenant in control.** No auto-sending. No "we recovered ₹X". Approve first,
   then prepare a letter.
5. **Fit the channel the tenant already uses** — WhatsApp — and the language they think in —
   Hindi, with English available.
6. **Be honest about the metric.** "Excess detected" and "flagged for review" are facts.
   "Money recovered" is not.

---

## What LeaseGuard is (and is not)

**Is:** a pre-payment check. Upload three documents, get a clause-backed list of
discrepancies, approve what you agree with, forward a ready letter.

**Is not:** a lawyer, a court filing, a recovery service, or a fraud accusation. The product
language, the UI, and the generated letter all say this explicitly.

---

## Success criteria for the hackathon demo

| Criterion | How this build satisfies it |
|---|---|
| Working solution | Live app: upload → audit → approve → WhatsApp letter, tested end-to-end |
| Traceability | Every finding shows document, page, effective date, quote, calculation |
| Precedence correctness | Amendment effective before the anniversary caps escalation at 5%, not 10% |
| Honesty | Uncertain findings are surfaced as uncertain; no recovery claims anywhere |
| Bharat fit | Hindi UI on key screens + a Hindi dispute letter; WhatsApp as primary channel |
| Reproducibility | Synthetic PDFs with real page numbers; deterministic fallback so the demo works offline |

---

## Scale of the opportunity (framing, not a claim)

India has tens of millions of MSME retail and service establishments, a large share of them
rented. Even a conservative estimate of ₹500–₹2,000 in annual over-charging per tenant puts
the aggregate leakage in the thousands of crores. LeaseGuard does not recover that money —
it makes the leakage **visible and contestable** at near-zero marginal cost, which is the
only realistic first step.

*Note: figures above are order-of-magnitude framing for the pitch, not measured findings.
The product's own metrics are computed per invoice and shown with an explicit disclaimer.*
