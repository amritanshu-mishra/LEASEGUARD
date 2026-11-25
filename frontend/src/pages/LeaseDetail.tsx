import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, looksLikeLostSession } from '../api'
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
  const [saving, setSaving] = useState(false)
  const [lostSession, setLostSession] = useState(false)

  const load = useCallback(async (date?: string) => {
    setErr('')
    try {
      const data = await api.getLease(leaseId!, date || undefined)
      setLease(data)
      setAsOf(prev => prev || data.as_of)
    } catch (e: any) {
      // A 404 here is nearly always a lost cookie rather than a deleted lease.
      // Saying "not found" leaves the user with no idea what happened; saying what
      // probably happened costs nothing and is usually right.
      setErr(looksLikeLostSession(e) ? t('lostSession') : (e.message || 'Failed to load lease'))
      setLostSession(looksLikeLostSession(e))
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

  /**
   * Record a value the tenant states for a term the document could not settle.
   *
   * Different from saveCorrection: there is no term to correct, so a new term is
   * created with no document attached. The audit re-runs immediately, which is
   * the point - an "uncertain" row that cannot be resolved is a dead end.
   */
  const saveUserValue = async (row: any) => {
    setEditError('')
    const raw = Number(editValue)
    if (!Number.isFinite(raw) || raw < 0) {
      setEditError(t('enterValidNumber'))
      return
    }
    setSaving(true)
    try {
      await api.addUserTerm(leaseId!, row.term_type, Math.round(raw * 100), t('correctionNote'))
      setEditing('')
      await load(asOf)
      setNotice(t('valueSaved'))
    } catch (e: any) {
      setEditError(e.message || t('saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  /** Adopt one of two contradictory clauses, in the tenant's own words. */
  const useConflict = async (row: any, choice: any) => {
    setEditError('')
    setSaving(true)
    try {
      // Carry the chosen clause's own effective date: the choice must take effect
      // when that clause did, or "latest clause wins" would discard it.
      await api.addUserTerm(
        leaseId!,
        row.term_type,
        choice.value,
        `${t('useThisClause')}: ${choice.document_name} (${choice.effective_date})`,
        choice.effective_date,
      )
      await load(asOf)
      setNotice(t('valueSaved'))
    } catch (e: any) {
      setEditError(e.message || t('saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="loading" role="status">{t('loadingLease')}</div>
  if (err && !lease) {
    return (
      <div className="card">
        <div className="error" role="alert" style={{ marginBottom: 16 }}>{err}</div>
        {lostSession && (
          // The lease the URL points at is not reachable from this browser. The
          // sample case can be re-created in one click, which at least leaves the
          // user with a working app rather than a dead end.
          <button
            className="btn btn-primary"
            onClick={async () => {
              try {
                const res = await api.loadSample()
                nav(`/leases/${res.lease_id}`)
                window.location.reload()
              } catch (e: any) {
                setErr(e.message || 'Could not reload the sample case')
              }
            }}
          >
            {t('reloadSample')}
          </button>
        )}
        <div style={{ marginTop: 16 }}>
          <Link to="/" className="backlink">← {t('dashboard')}</Link>
        </div>
      </div>
    )
  }
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
                      <td>
                        <span className={`badge ${d.kind === 'invoice' ? 'badge-gray' : d.kind === 'amendment' ? 'badge-amber' : 'badge-teal'}`}>{d.kind}</span>
                        {/* A scan is not the same evidence as a text PDF. Say so on the
                            document itself, not only in the upload response. */}
                        {d.ocr ? <span className="badge badge-amber" style={{ marginLeft: 6 }} title={t('scanCheckTerms')}>🔍 {t('readFromScan')}</span> : null}
                      </td>
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
                            placeholder={row.status === 'resolved' ? undefined : t('enterValuePrompt')}
                          />
                          <button
                            className="btn btn-primary btn-xs"
                            disabled={saving}
                            onClick={() => (row.status === 'resolved' ? saveCorrection(row) : saveUserValue(row))}
                          >✓</button>
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
                          {row.entered_by_user && (
                            <span className="badge badge-gray" title={t('enteredHint')}>✎ {t('enteredByYou')}</span>
                          )}
                          {/* A resolved term can be corrected. An unresolved one used to
                              offer nothing at all, which left "uncertain" as a dead end. */}
                          {row.term_id && row.status === 'resolved' && (
                            <button
                              className="btn btn-ghost btn-xs term-fix"
                              onClick={() => {
                                setEditing(row.term_type)
                                // Storage is paise / basis points; the editor shows rupees / percent.
                                // Both scale by 100, so one expression covers both.
                                setEditValue(String((row.value ?? 0) / 100))
                              }}
                              aria-label={`${t('fixValue')} — ${termLabel(row.term_type)}`}
                            >
                              ✎ {t('fixValue')}
                            </button>
                          )}
                          {row.status !== 'resolved' && !editing && (
                            <button
                              className="btn btn-ghost btn-xs term-fix term-provide"
                              onClick={() => {
                                setEditing(row.term_type)
                                setEditValue('')
                              }}
                              aria-label={`${t('provideValue')} — ${termLabel(row.term_type)}`}
                            >
                              ✎ {t('provideValue')}
                            </button>
                          )}
                        </div>
                      )}
                      {editError && editing === row.term_type && (
                        <div className="error" role="alert" style={{ marginTop: 6 }}>{editError}</div>
                      )}
                    </td>
                    <td style={{ fontSize: 13 }}>
                      {row.entered_by_user ? (
                        <span className="muted">{t('enteredHint')}</span>
                      ) : row.status === 'resolved' ? (
                        <>{row.document_name}<span className="muted">, {t('page')} {row.page}</span> <span className="badge badge-teal">{t('controls')}</span></>
                      ) : (
                        <div>
                          <span className="muted">{row.reason || '—'}</span>
                          {/* Two amendments that disagree: the tenant decides which
                              applies, rather than the product guessing. */}
                          {row.conflicts?.length > 0 && (
                            <div style={{ marginTop: 6 }}>
                              <div className="muted" style={{ marginBottom: 4 }}>{t('conflictingClauses')}</div>
                              {row.conflicts.map((c: any, i: number) => (
                                <div key={i} className="conflict-option">
                                  <span>{fmtTermValue(row.term_type, c.value)}</span>
                                  <span className="muted"> — {c.document_name}, {c.effective_date}</span>
                                  <button
                                    className="btn btn-ghost btn-xs term-use-conflict"
                                    disabled={saving}
                                    onClick={() => useConflict(row, c)}
                                  >{t('useThisClause')}</button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
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
