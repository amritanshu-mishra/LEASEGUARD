/**
 * Single source of truth for money display in the UI.
 *
 * Amounts cross the API as integer paise. The API also returns pre-formatted
 * "_display" strings for non-UI consumers, but the UI formats from paise so the whole
 * screen uses one currency style (₹ + Indian digit grouping) and one rounding rule.
 */
export function inr(paise: number | null | undefined, opts: { decimals?: number } = {}): string {
  if (paise == null) return '—'
  const decimals = opts.decimals ?? 0
  return `₹${(paise / 100).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

/** Indian grouping with two decimals, for itemised amounts where paise matter. */
export function inr2(paise: number | null | undefined): string {
  return inr(paise, { decimals: 2 })
}

/** The backend already formats some strings ("Rs. 3,200.00"); normalise to ₹ style. */
export function normaliseInr(display: string | null | undefined): string {
  if (!display) return '—'
  return display.replace(/^Rs\.?\s*/, '₹').replace(/^INR\s*/, '₹')
}

/** Normalise "Rs. 1,200.00" / "INR 500" inside prose coming from the backend. */
export function normaliseProse(text: string | null | undefined): string {
  if (!text) return ''
  return text.replace(/\bRs\.?\s*/g, '₹').replace(/\bINR\s*/g, '₹')
}

export function formatDate(value: string | number | null | undefined): string {
  if (value == null) return '—'
  const s = String(value)
  if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
  return s
}
