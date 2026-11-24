import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'
import type { StringKey } from '../i18n'

type UploadRow = { kind: 'lease' | 'amendment' | 'invoice'; labelKey: StringKey; file: File | null; effective: string }

const ROWS: UploadRow[] = [
  { kind: 'lease', labelKey: 'leaseAgreement', file: null, effective: '' },
  { kind: 'amendment', labelKey: 'amendmentOpt', file: null, effective: '' },
  { kind: 'invoice', labelKey: 'monthlyInvoice', file: null, effective: '' },
]

export default function NewLeasePage() {
  const nav = useNavigate()
  const { t } = useLang()
  const [label, setLabel] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [rows, setRows] = useState<UploadRow[]>(ROWS)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState('')
  const [err, setErr] = useState('')
  const [warn, setWarn] = useState<string[]>([])

  const setRow = (kind: string, patch: Partial<UploadRow>) =>
    setRows(rs => rs.map(r => (r.kind === kind ? { ...r, ...patch } : r)))

  const submit = async () => {
    setErr(''); setWarn([])
    if (label.trim().length < 3) { setErr(t('enterProperty')); return }
    if (!rows[0].file) { setErr(t('chooseLease')); return }
    if (!rows[2].file) { setErr(t('chooseInvoice')); return }
    setBusy(true)
    try {
      setStep(t('creatingLease'))
      const created = await api.createLease({
        property_label: label.trim(),
        start_date: start || undefined,
        end_date: end || undefined,
      })
      const leaseId = created.lease_id
      const notes: string[] = []
      for (const r of rows) {
        if (!r.file) continue
        setStep(`${t('reading')} ${r.kind}…`)
        const res = await api.uploadDocument(leaseId, r.kind, r.file, r.effective || undefined)
        if (res.uncertain_terms?.length) {
          notes.push(`${t('uncertainNote')} (${res.uncertain_terms.join(', ')})`)
        }
        if (res.extractor === 'heuristic') {
          notes.push(`${t('heuristicNote')} (${r.kind})`)
        }
        if (res.invoice_id) {
          setStep(t('auditingInvoice'))
          await api.runAudit(leaseId, res.invoice_id)
          nav(`/leases/${leaseId}/audit/${res.invoice_id}`)
          return
        }
      }
      setWarn(notes)
      nav(`/leases/${leaseId}`)
    } catch (e: any) {
      setErr(e.message || 'Upload failed')
    } finally {
      setBusy(false); setStep('')
    }
  }

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">{t('newLease')}</h1>
          <p className="page-sub">{t('newLeaseSub')}</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => nav('/')}>← {t('dashboard')}</button>
      </div>

      {err && <div className="error" style={{ marginBottom: 16 }}>{err}</div>}
      {warn.map((w, i) => <div key={i} className="notice" style={{ marginBottom: 10 }}>{w}</div>)}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title">{t('propertyDetails')}</h2>
        <div className="field">
          <label>{t('propertyName')}</label>
          <input value={label} onChange={e => setLabel(e.target.value)} placeholder={t('propertyPlaceholder')} />
        </div>
        <div className="row" style={{ gap: 12 }}>
          <div className="field" style={{ flex: 1 }}>
            <label>{t('leaseStartOpt')}</label>
            <input type="date" value={start} onChange={e => setStart(e.target.value)} />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>{t('leaseEndOpt')}</label>
            <input type="date" value={end} onChange={e => setEnd(e.target.value)} />
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title">{t('documentsStep')}</h2>
        {rows.map(r => (
          <div key={r.kind} className="upload-row">
            <div style={{ minWidth: 160, fontWeight: 700, color: 'var(--navy)' }}>{t(r.labelKey)}</div>
            <input type="file" accept=".pdf,.txt,.md" onChange={e => setRow(r.kind, { file: e.target.files?.[0] || null })} />
            <input type="date" value={r.effective} onChange={e => setRow(r.kind, { effective: e.target.value })} title="Effective date (used for precedence)" />
            {r.file && <span className="badge badge-teal">{r.file.name.slice(0, 28)}</span>}
          </div>
        ))}
        <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          {t('effectiveDateHelp')}
        </p>
      </div>

      <div className="card row space-between">
        <div className="muted" style={{ fontSize: 14 }}>{t('privacyNote')}</div>
        <div className="row">
          <button className="btn btn-ghost" onClick={() => nav('/')} disabled={busy}>{t('cancel')}</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? step || 'Working…' : `📤 ${t('uploadDocument')}`}</button>
        </div>
      </div>
    </div>
  )
}
