import { useRef, useState } from 'react'
import { api } from '../api'
import { useLang } from '../i18nContext'
import { inr2, formatDate } from '../format'

export type DocKind = 'lease' | 'amendment' | 'invoice'

type Props = {
  leaseId: string
  /** Which kinds may be uploaded here. Lease detail allows invoice + amendment only. */
  allow?: DocKind[]
  onUploaded: (kind: DocKind, result: any) => void
}

const LABEL_KEY: Record<DocKind, 'leaseAgreement' | 'amendmentOpt' | 'monthlyInvoice'> = {
  lease: 'leaseAgreement',
  amendment: 'amendmentOpt',
  invoice: 'monthlyInvoice',
}

/**
 * Upload one document to an existing lease.
 *
 * Deliberately explicit about three things the earlier flow got wrong:
 * - the effective date is required (it decides precedence, so a default is a lie);
 * - the file is read and its extracted terms are shown before the user moves on;
 * - any warning from the server is surfaced, not swallowed.
 */
export function UploadDocument({ leaseId, allow = ['invoice', 'amendment'], onUploaded }: Props) {
  const { t } = useLang()
  const inputRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<DocKind>(allow[0])
  const [effective, setEffective] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [summary, setSummary] = useState<{ label: string; value: string }[]>([])

  const reset = () => {
    setFile(null)
    setEffective('')
    setWarnings([])
    setSummary([])
    setError('')
    if (inputRef.current) inputRef.current.value = ''
  }

  const submit = async () => {
    setError('')
    setWarnings([])
    setSummary([])
    if (!file) { setError(t('chooseFileFirst')); return }
    if (!effective) { setError(t('chooseEffectiveDate')); return }
    setBusy(true)
    try {
      const res = await api.uploadDocument(leaseId, kind, file, effective)
      const rows: { label: string; value: string }[] = []
      if (res.line_items?.length) {
        for (const li of res.line_items) {
          rows.push({ label: li.description, value: inr2(li.amount_paise) })
        }
      }
      if (res.terms?.length) {
        for (const term of res.terms) {
          rows.push({
            label: termLabel(term.term_type, t),
            value: prettyTermValue(term.term_type, term.value, term.ambiguous),
          })
        }
      }
      setSummary(rows)
      setWarnings(res.warnings || [])
      onUploaded(kind, res)
      if (!res.warnings?.length) reset()
    } catch (e: any) {
      setError(e.message || t('uploadFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="uploader">
      <div className="uploader-head">
        <h3 className="section-title" style={{ margin: 0 }}>{t('addDocument')}</h3>
        <span className="muted" style={{ fontSize: 13 }}>{t('addDocumentHint')}</span>
      </div>

      <div className="uploader-row">
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`kind-${leaseId}`}>{t('documentType')}</label>
          <select
            id={`kind-${leaseId}`}
            value={kind}
            onChange={e => { setKind(e.target.value as DocKind); reset() }}
          >
            {allow.map(k => <option key={k} value={k}>{t(LABEL_KEY[k])}</option>)}
          </select>
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`eff-${leaseId}`}>{t('effectiveDate')}</label>
          <input
            id={`eff-${leaseId}`}
            type="date"
            value={effective}
            onChange={e => setEffective(e.target.value)}
          />
        </div>

        <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 200 }}>
          <label htmlFor={`file-${leaseId}`}>{t('chooseFile')}</label>
          <input
            id={`file-${leaseId}`}
            ref={inputRef}
            type="file"
            accept=".pdf,.txt,.md,application/pdf,text/plain"
            onChange={e => { setFile(e.target.files?.[0] || null); setError(''); setSummary([]) }}
          />
        </div>

        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? t('readingDocument') : `⬆ ${t('uploadDocument')}`}
        </button>
      </div>

      {file && <p className="muted uploader-file">{t('selectedFile')}: <strong>{file.name}</strong></p>}

      {error && <div className="error" role="alert" style={{ marginTop: 10 }}>{error}</div>}

      {warnings.map((w, i) => (
        <div key={i} className="notice" role="status" style={{ marginTop: 10 }}>{w}</div>
      ))}

      {summary.length > 0 && (
        <div className="uploader-result" role="status">
          <div className="uploader-result-title">{t('extractedFromFile')}</div>
          <ul>
            {summary.map((row, i) => (
              <li key={i}><span>{row.label}</span><strong>{row.value}</strong></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function termLabel(termType: string, t: (k: any) => string): string {
  switch (termType) {
    case 'base_rent_paise': return t('baseRent')
    case 'escalation_rate_bp': return t('escalation')
    case 'maintenance_cap_paise': return t('maintenanceCap')
    case 'lease_start_date': return t('leaseStartOpt')
    case 'amendment_effective_date': return t('effectiveDate')
    default: return termType.replace(/_/g, ' ')
  }
}

function prettyTermValue(termType: string, value: number | null, ambiguous?: boolean): string {
  if (ambiguous || value == null) return '⚠ ' + 'Not read'
  if (termType === 'escalation_rate_bp') return `${(value / 100).toFixed(2)}%`
  if (termType === 'base_rent_paise' || termType === 'maintenance_cap_paise') {
    return `₹${(value / 100).toLocaleString('en-IN')}`
  }
  if (termType === 'lease_start_date' || termType === 'amendment_effective_date') {
    return formatDate(value)
  }
  return String(value)
}
