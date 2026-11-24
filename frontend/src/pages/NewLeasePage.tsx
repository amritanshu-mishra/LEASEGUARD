import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'
import { inr2, inr, formatDate } from '../format'

type Kind = 'lease' | 'amendment' | 'invoice'

type Slot = {
  kind: Kind
  labelKey: 'leaseAgreement' | 'amendmentOpt' | 'monthlyInvoice'
  file: File | null
  effective: string
  required: boolean
}

type SlotState = 'idle' | 'uploading' | 'done' | 'warning' | 'error'

const initialSlots = (): Slot[] => [
  { kind: 'lease', labelKey: 'leaseAgreement', file: null, effective: '', required: true },
  { kind: 'amendment', labelKey: 'amendmentOpt', file: null, effective: '', required: false },
  { kind: 'invoice', labelKey: 'monthlyInvoice', file: null, effective: '', required: true },
]

export default function NewLeasePage() {
  const nav = useNavigate()
  const { t } = useLang()
  const [label, setLabel] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [slots, setSlots] = useState<Slot[]>(initialSlots)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState('')
  const [error, setError] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [slotState, setSlotState] = useState<Record<string, SlotState>>({})
  const [results, setResults] = useState<Record<string, { label: string; value: string }[]>>({})

  const patch = (kind: Kind, p: Partial<Slot>) =>
    setSlots(prev => prev.map(s => (s.kind === kind ? { ...s, ...p } : s)))

  const validate = (): string => {
    if (label.trim().length < 3) return t('enterProperty')
    const lease = slots.find(s => s.kind === 'lease')!
    const invoice = slots.find(s => s.kind === 'invoice')!
    if (!lease.file) return t('chooseLease')
    if (!invoice.file) return t('chooseInvoice')
    for (const s of slots) {
      if (s.file && !s.effective) return `${t('chooseEffectiveDate')} (${t(s.labelKey)})`
    }
    // The invoice cannot be effective before the lease starts.
    if (start && invoice.effective && invoice.effective < start) {
      return t('invoiceBeforeLease')
    }
    return ''
  }

  const submit = async () => {
    setError(''); setWarnings([]); setResults({})
    const problem = validate()
    if (problem) { setError(problem); return }

    setBusy(true)
    try {
      setStep(t('creatingLease'))
      const created = await api.createLease({
        property_label: label.trim(),
        start_date: start || undefined,
        end_date: end || undefined,
      })
      const leaseId: string = created.lease_id
      let invoiceId: string | null = null
      const collectedWarnings: string[] = []

      for (const slot of slots) {
        if (!slot.file) continue
        setSlotState(s => ({ ...s, [slot.kind]: 'uploading' }))
        setStep(`${t('reading')} ${t(slot.labelKey)}…`)
        try {
          const res = await api.uploadDocument(leaseId, slot.kind, slot.file, slot.effective)
          const rows: { label: string; value: string }[] = []
          for (const li of res.line_items || []) {
            rows.push({ label: li.description, value: inr2(li.amount_paise) })
          }
          for (const term of res.terms || []) {
            if (term.ambiguous || term.value == null) continue
            rows.push({ label: prettyLabel(term.term_type), value: prettyValue(term.term_type, term.value) })
          }
          setResults(r => ({ ...r, [slot.kind]: rows }))
          setSlotState(s => ({ ...s, [slot.kind]: res.warnings?.length ? 'warning' : 'done' }))
          if (res.warnings?.length) collectedWarnings.push(...res.warnings)
          if (res.invoice_id) invoiceId = res.invoice_id
        } catch (e: any) {
          setSlotState(s => ({ ...s, [slot.kind]: 'error' }))
          throw new Error(`${t(slot.labelKey)}: ${e.message}`)
        }
      }

      setWarnings(collectedWarnings)

      if (invoiceId) {
        setStep(t('auditingInvoice'))
        await api.runAudit(leaseId, invoiceId)
        setStep(t('openingAudit'))
        nav(`/leases/${leaseId}/audit/${invoiceId}`)
        return
      }
      nav(`/leases/${leaseId}`)
    } catch (e: any) {
      setError(e.message || t('uploadFailed'))
    } finally {
      setBusy(false)
      setStep('')
    }
  }

  const prettyLabel = (tt: string) => {
    switch (tt) {
      case 'base_rent_paise': return t('baseRent')
      case 'escalation_rate_bp': return t('escalation')
      case 'maintenance_cap_paise': return t('maintenanceCap')
      case 'lease_start_date': return t('leaseStartOpt')
      case 'amendment_effective_date': return t('effectiveDate')
      default: return tt.replace(/_/g, ' ')
    }
  }
  const prettyValue = (tt: string, v: number) => {
    if (tt === 'escalation_rate_bp') return `${(v / 100).toFixed(2)}% ${t('perYear')}`
    if (tt === 'base_rent_paise' || tt === 'maintenance_cap_paise') return inr(v)
    return formatDate(v)
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <Link to="/" className="backlink">← {t('dashboard')}</Link>
          <h1 className="page-title">{t('newLease')}</h1>
          <p className="page-sub">{t('newLeaseSub')}</p>
        </div>
      </div>

      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
      {warnings.map((w, i) => <div key={i} className="notice" role="status" style={{ marginBottom: 10 }}>{w}</div>)}

      <ol className="steps">
        <li className={label.trim().length >= 3 ? 'done' : 'current'}>1. {t('propertyDetails')}</li>
        <li className={label.trim().length >= 3 ? 'current' : ''}>2. {t('documentsStep')}</li>
        <li>3. {t('auditInvoice')}</li>
      </ol>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title">{t('propertyDetails')}</h2>
        <div className="field">
          <label htmlFor="property-label">{t('propertyName')}</label>
          <input
            id="property-label"
            value={label}
            onChange={e => setLabel(e.target.value)}
            placeholder={t('propertyPlaceholder')}
            autoComplete="off"
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="lease-start">{t('leaseStartOpt')}</label>
            <input id="lease-start" type="date" value={start} onChange={e => setStart(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="lease-end">{t('leaseEndOpt')}</label>
            <input id="lease-end" type="date" value={end} onChange={e => setEnd(e.target.value)} />
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title">{t('documentsStep')}</h2>
        <p className="muted" style={{ fontSize: 13, marginTop: -6, marginBottom: 14 }}>{t('effectiveDateHelp')}</p>

        {slots.map(slot => {
          const state = slotState[slot.kind] || 'idle'
          return (
            <div key={slot.kind} className={`slot slot-${state}`}>
              <div className="slot-head">
                <span className="slot-title">
                  {t(slot.labelKey)}
                  {slot.required
                    ? <span className="req" aria-label={t('required')}>*</span>
                    : <span className="badge badge-gray" style={{ marginLeft: 8 }}>{t('optional')}</span>}
                </span>
                {state === 'done' && <span className="badge badge-green">✓ {t('uploadSuccess')}</span>}
                {state === 'warning' && <span className="badge badge-amber">! {t('checkWarnings')}</span>}
                {state === 'uploading' && <span className="badge badge-teal">⏳ {t('readingDocument')}</span>}
                {state === 'error' && <span className="badge badge-red">✕ {t('uploadFailed')}</span>}
              </div>

              <div className="slot-inputs">
                <div className="field">
                  <label htmlFor={`file-${slot.kind}`}>{t('chooseFile')}</label>
                  <input
                    id={`file-${slot.kind}`}
                    type="file"
                    accept=".pdf,.txt,.md,application/pdf,text/plain"
                    disabled={busy}
                    onChange={e => {
                      patch(slot.kind, { file: e.target.files?.[0] || null })
                      setSlotState(s => ({ ...s, [slot.kind]: 'idle' }))
                      setError('')
                    }}
                  />
                </div>
                <div className="field">
                  <label htmlFor={`eff-${slot.kind}`}>{t('effectiveDate')}</label>
                  <input
                    id={`eff-${slot.kind}`}
                    type="date"
                    value={slot.effective}
                    disabled={busy}
                    onChange={e => patch(slot.kind, { effective: e.target.value })}
                  />
                </div>
              </div>

              {slot.file && <p className="slot-file">{slot.file.name}</p>}

              {results[slot.kind]?.length > 0 && (
                <ul className="slot-result">
                  {results[slot.kind].map((row, i) => (
                    <li key={i}><span>{row.label}</span><strong>{row.value}</strong></li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>

      <div className="card action-bar">
        <div className="muted" style={{ fontSize: 13, maxWidth: 460 }}>{t('privacyNote')}</div>
        <div className="row">
          <button className="btn btn-ghost" onClick={() => nav('/')} disabled={busy}>{t('cancel')}</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? (step || t('loading')) : `⬆ ${t('uploadDocument')}`}
          </button>
        </div>
      </div>
    </div>
  )
}
