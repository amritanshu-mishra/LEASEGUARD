import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'
import { UploadDocument } from '../components/UploadDocument'

import { inr } from '../format'


export default function LeaseDetail() {
  const { leaseId } = useParams()
  const nav = useNavigate()
  const { t } = useLang()
  const [lease, setLease] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [asOf, setAsOf] = useState('')
  const [notice, setNotice] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState('')
  const [editValue, setEditValue] = useState('')
  const [editError, setEditError] = useState('')

  const load = useCallback(async (date?: string) => {
    setErr('')
    try {
      const data = await api.getLease(leaseId!, date || undefined)
      setLease(data)
      setAsOf(prev => prev || data.as_of)
    } catch (e: any) {
      setErr(e.message || 'Failed to load lease')
    } finally {
      setLoading(false)
    }
  }, [leaseId])

  useEffect(() => { load() }, [load])

  const termLabel = (tt: string) =>
    tt === 'base_rent_paise' ? t('baseRent')
      : tt === 'escalation_rate_bp' ? t('escalation')
      : tt === 'maintenance_cap_paise' ? t('maintenanceCap')
      : tt

  const fmtTermValue = (tt: string, v: number | null | undefined) => {
    if (v == null) return '—'
    if (tt === 'escalation_rate_bp') return `${(v / 100).toFixed(2)}% ${t('perYear')}`
    if (tt === 'base_rent_paise' || tt === 'maintenance_cap_paise') return inr(v)
    return String(v)
  }

  const onUploaded = async (kind: string, res: any) => {
    setNotice(kind === 'invoice' ? t('invoiceAdded') : t('documentAdded'))
    await load(asOf)
    if (res?.invoice_id) {
      nav(`/leases/${leaseId}/audit/${res.invoice_id}`)
    }
  }

  const removeLease = async () => {
    try {
      await api.deleteLease(leaseId!)
      nav('/')
    } catch (e: any) {
      setErr(e.message || 'Could not delete')
      setConfirmDelete(false)
    }
  }

  const saveCorrection = async (row: any) => {
    setEditError('')
    const raw = Number(editValue)
    if (!Number.isFinite(raw) || raw < 0) {
      setEditError(t('enterValidNumber'))
      return
    }
    // The editor takes rupees/percent; storage is paise/basis points.
    const stored = Math.round(raw * 100)
    try {
      await api.correctTerm(row.term_id, stored, t('correctionNote'))
      setEditing('')
      await load(asOf)
      setNotice(t('correctionSaved'))
    } catch (e: any) {
      setEditError(e.message || t('saveFailed'))
    }
  }

  if (loading) return <div className="loading" role="status">{t('loadingLease')}</div>
  if (err && !lease) return <div className="error" role="alert">{err}</div>
  if (!lease) return <div className="error">{t('leaseNotFound')}</div>

  return (
    <div>
      <div className="page-head">
        <div>
          <Link to="/" className="backlink">← {t('dashboard')}</Link>
          <h1 className="page-title">{lease.property_label}</h1>
          <p className="page-sub">{t('leasePeriod')}: {lease.start_date} → {lease.end_date}</p>
        </div>
        <div className="page-actions">
          <label className="inline-field">
            <span>{t('showTermsAsOf')}</span>
            <input
              type="date"
              value={asOf}
              onChange={e => { setAsOf(e.target.value); load(e.target.value) }}
              aria-label={t('showTermsAsOf')}
            />
          </label>
          {confirmDelete ? (
            <div className="row">
              <button className="btn btn-danger btn-sm" onClick={removeLease}>✓ {t('delete')}</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>{t('cancel')}</button>
            </div>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(true)}>🗑 {t('deleteLease')}</button>
          )}
        </div>
      </div>

      {err && <div className="error" role="alert" style={{ marginBottom: 16 }}>{err}</div>}
      {notice && <div className="notice" role="status" style={{ marginBottom: 16 }}>{notice}</div>}

      <div className="grid grid-2">
        <div className="card">
          <h2 className="section-title">{t('documents')}</h2>
          {lease.documents.length === 0 ? <p className="muted">{t('noDocuments')}</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>{t('name')}</th><th>{t('type')}</th><th>{t('effectiveDate')}</th></tr></thead>
                <tbody>
                  {lease.documents.map((d: any) => (
                    <tr key={d.id}>
                      <td className="doc-name">{d.name}</td>
                      <td><span className={`badge ${d.kind === 'invoice' ? 'badge-gray' : d.kind === 'amendment' ? 'badge-amber' : 'badge-teal'}`}>{d.kind}</span></td>
                      <td>{d.effective_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="section-title">
            {t('termsInForce')} <span className="as-of-chip">{lease.as_of}</span>
          </h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('term')}</th><th>{t('value')}</th><th>{t('source')}</th></tr></thead>
              <tbody>
                {lease.terms_in_force.map((row: any) => (
                  <tr key={row.term_type}>
                    <td>{termLabel(row.term_type)}</td>
                    <td>
                      {editing === row.term_type ? (
                        <div className="term-edit">
                          <input
                            type="number"
                            className="term-input"
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            aria-label={`${t('correctValue')} — ${termLabel(row.term_type)}`}
                            min={0}
                          />
                          <button className="btn btn-primary btn-xs" onClick={() => saveCorrection(row)}>✓</button>
                          <button className="btn btn-ghost btn-xs" onClick={() => setEditing('')}>✕</button>
                        </div>
                      ) : (
                        <div className="term-value-cell">
                          {row.status === 'resolved'
                            ? <strong>{fmtTermValue(row.term_type, row.value)}</strong>
                            : <span className="badge badge-amber">? {t('uncertain')}</span>}
                          {row.corrected_by_user && (
                            <span className="badge badge-teal" title={t('correctedHint')}>✎ {t('correctedByYou')}</span>
                          )}
                          {row.term_id && (
                            <button
                              className="btn btn-ghost btn-xs term-fix"
                              onClick={() => {
                                setEditing(row.term_type)
                                // Values are stored in paise / basis points; the editor shows rupees / percent.
                                setEditValue(String(
                                  row.term_type === 'escalation_rate_bp'
                                    ? (row.value ?? 0) / 100
                                    : (row.value ?? 0) / 100
                                ))
                              }}
                              aria-label={`${t('fixValue')} — ${termLabel(row.term_type)}`}
                            >
                              ✎ {t('fixValue')}
                            </button>
                          )}
                        </div>
                      )}
                      {editError && editing === row.term_type && (
                        <div className="error" role="alert" style={{ marginTop: 6 }}>{editError}</div>
                      )}
                    </td>
                    <td style={{ fontSize: 13 }}>
                      {row.status === 'resolved'
                        ? <>{row.document_name}<span className="muted">, {t('page')} {row.page}</span> <span className="badge badge-teal">{t('controls')}</span></>
                        : <span className="muted">{row.reason || '—'}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {lease.superseded_terms?.length > 0 && (
            <details className="superseded">
              <summary>{t('superseded')} ({lease.superseded_terms.length})</summary>
              {lease.superseded_terms.map((s: any, i: number) => (
                <div key={i} className="superseded-item">
                  <span className="struck">{termLabel(s.term_type)}: {fmtTermValue(s.term_type, s.value)}</span>
                  <span className="muted"> — {s.document_name}, {t('page')} {s.page}, {s.effective_date}</span>
                </div>
              ))}
            </details>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="row space-between" style={{ marginBottom: 12 }}>
          <h2 className="section-title" style={{ margin: 0 }}>{t('invoices')}</h2>
        </div>
        {lease.invoices.length === 0 ? (
          <div className="empty-inline">
            <p>{t('noInvoices')}</p>
            <p className="muted">{t('addInvoiceHint')}</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('period')}</th><th>{t('items')}</th><th>{t('status')}</th><th aria-label="actions"></th></tr></thead>
              <tbody>
                {lease.invoices.map((inv: any) => (
                  <tr key={inv.id}>
                    <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{inv.period_label || inv.period_start}</td>
                    <td className="invoice-items">
                      {inv.line_items?.map((li: any, i: number) => (
                        <span key={i} className="pill">{li.description}: {inr(li.amount_paise)}</span>
                      ))}
                    </td>
                    <td>
                      {inv.findings_count > 0 ? (
                        <span className="row" style={{ gap: 6 }}>
                          {inv.confirmed_count > 0 && <span className="badge badge-red">⚠ {inv.confirmed_count} {t('discrepancy')}</span>}
                          {inv.uncertain_count > 0 && <span className="badge badge-amber">? {inv.uncertain_count} {t('uncertain')}</span>}
                          {inv.confirmed_count === 0 && inv.uncertain_count === 0 && <span className="badge badge-green">✓ {t('noIssues')}</span>}
                        </span>
                      ) : <span className="badge badge-gray">{t('notAudited')}</span>}
                    </td>
                    <td className="num" style={{ whiteSpace: 'nowrap' }}>
                      <Link className="btn btn-primary btn-sm" to={`/leases/${lease.id}/audit/${inv.id}`}>
                        🔍 {inv.findings_count > 0 ? t('viewAudit') : t('auditInvoice')}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <UploadDocument leaseId={lease.id} allow={['invoice', 'amendment']} onUploaded={onUploaded} />
      </div>
    </div>
  )
}
