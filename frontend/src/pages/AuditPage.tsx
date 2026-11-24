import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'

function inr(paise: number | null | undefined) {
  if (paise == null) return '—'
  return `₹${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function AuditPage() {
  const { leaseId, invoiceId } = useParams()
  const nav = useNavigate()
  const { t } = useLang()
  const [invoice, setInvoice] = useState<any>(null)
  const [findings, setFindings] = useState<any[]>([])
  const [impact, setImpact] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [viewQuote, setViewQuote] = useState<Record<string, boolean>>({})

  const loadFromServer = async () => {
    const fd = await api.getFindings(invoiceId!)
    setInvoice(fd.invoice)
    setFindings(fd.findings || [])
    setImpact(fd.impact || null)
    return fd
  }

  const runAudit = async () => {
    setErr('')
    const d = await api.runAudit(leaseId!, invoiceId)
    setFindings(d.findings || [])
    setImpact(d.impact || null)
    await loadFromServer()
  }

  useEffect(() => {
    (async () => {
      setLoading(true); setErr('')
      try {
        const fd = await loadFromServer()
        // First visit: no findings stored yet -> run the audit automatically.
        if ((fd.findings || []).length === 0) await runAudit()
      } catch (e: any) { setErr(e.message || 'Failed to load audit') }
      finally { setLoading(false) }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaseId, invoiceId])

  const decide = async (findingId: string, approved: boolean) => {
    setBusyId(findingId); setErr('')
    try {
      await api.decideFinding(findingId, approved)
      await loadFromServer()
    } catch (e: any) { setErr(e.message || 'Failed to update finding') }
    finally { setBusyId(null) }
  }

  const actionable = useMemo(() => findings.filter(f => !f.informational), [findings])
  const approvedCount = useMemo(() => actionable.filter(f => f.approved === true).length, [actionable])
  const confirmedCount = actionable.filter(f => f.status === 'confirmed').length
  const uncertainCount = actionable.filter(f => f.status === 'uncertain').length
  const noIssueCount = findings.filter(f => f.informational || f.status === 'no_issue').length

  if (loading) {
    return (
      <div className="loading" role="status" aria-live="polite">
        {t('loadingAudit')}
      </div>
    )
  }

  const checkLabel = (ct: string) =>
    ct === 'rent_escalation' ? t('escalation') : ct === 'maintenance_cap' ? t('maintenanceCap') : ct.replace(/_/g, ' ')

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">{t('auditTitle')}{invoice ? ` — ${invoice.period_label}` : ''}</h1>
          <p className="page-sub">{t('auditSub')}</p>
        </div>
        <div className="row">
          <Link to={`/leases/${leaseId}`} className="btn btn-ghost btn-sm">← {t('back')}</Link>
          <button className="btn btn-secondary btn-sm" onClick={() => { setLoading(true); runAudit().catch((e: any) => setErr(e.message)).finally(() => setLoading(false)) }}>
            ↻ {t('rerun')}
          </button>
        </div>
      </div>

      {err && (
        <div className="error" role="alert" aria-live="assertive" style={{ marginBottom: 16 }}>
          {err}
        </div>
      )}

      {impact && (
        <div style={{ marginBottom: 16 }}>
          <div className="info" style={{ marginBottom: 12 }}>
            <strong>{t('impactTitle')}:</strong> {t('impactNote')}
          </div>
          <div className="stat-grid" role="group" aria-label={t('impactTitle')}>
            <div className="stat">
              <div className="stat-label">{t('excessDetected')}</div>
              <div className={`stat-value ${impact.excess_detected_paise > 0 ? 'red' : 'green'}`}>{impact.excess_detected_display}</div>
              <div className="stat-note">{t('excessNote')}</div>
            </div>
            <div className="stat">
              <div className="stat-label">{t('flaggedForReview')}</div>
              <div className={`stat-value ${impact.amount_flagged_paise > 0 ? 'red' : 'green'}`}>{impact.amount_flagged_display}</div>
              <div className="stat-note">{t('flaggedNote')}</div>
            </div>
            <div className="stat">
              <div className="stat-label">{t('timeSaved')}</div>
              <div className="stat-value">{impact.time_saved_display}</div>
              <div className="stat-note" title={impact.time_saved_basis || ''}>{t('timeNote')}</div>
            </div>
            <div className="stat">
              <div className="stat-label">{t('findings')}</div>
              <div className="stat-value">{confirmedCount + uncertainCount}</div>
              <div className="stat-note">
                {confirmedCount} {t('findingsNote')} {uncertainCount} {t('needsInput')}
                {noIssueCount > 0 && ` · ${noIssueCount} ${t('checkedNoAction').toLowerCase()}`}
              </div>
            </div>

          </div>
        </div>
      )}

      {invoice && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2 className="section-title">{t('invoiceSummary')}</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('lineItem')}</th><th className="num">{t('amount')}</th><th>{t('type')}</th></tr></thead>
              <tbody>
                {invoice.line_items.map((li: any, i: number) => (
                  <tr key={i}>
                    <td>{li.description}</td>
                    <td className="num">{inr(li.amount_paise)}</td>
                    <td><span className="badge badge-gray">{li.kind}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <h2 className="section-title" id="findings-heading">{t('findings')}</h2>
      {findings.length === 0 && <div className="empty">{t('noFindings')}</div>}

      {findings.map((f: any) => {
        const ct = f.controlling_term || {}
        const isConfirmed = f.status === 'confirmed'
        const isUncertain = f.status === 'uncertain'
        const isInfo = !!f.informational || f.status === 'no_issue'
        const quoteOpen = viewQuote[f.id] ?? true
        return (
          <article
            key={f.id}
            className={`finding ${isConfirmed ? 'confirmed' : isUncertain ? 'uncertain' : 'no_issue'}`}
            aria-labelledby={`finding-title-${f.id}`}
          >
            {isInfo && (
              <div className="badge badge-green" style={{ marginBottom: 10 }}>
                ✓ {t('checkedNoAction')}
              </div>
            )}
            <div className="finding-head">
              <div>
                <div style={{ marginBottom: 6 }}>
                  <span className={`badge ${isConfirmed ? 'badge-red' : isUncertain ? 'badge-amber' : 'badge-green'}`}>
                    {isConfirmed ? `⚠ ${t('discrepancyBadge')}` : isUncertain ? `? ${t('uncertainBadge')}` : `✓ ${t('noIssueBadge')}`}
                  </span>
                  {isInfo && <span className="badge badge-gray" style={{ marginLeft: 8 }}>{t('informational')}</span>}
                  <span className="badge badge-gray" style={{ marginLeft: 8 }}>{checkLabel(f.check_type)}</span>
                </div>
                <h3 className="finding-title" id={`finding-title-${f.id}`}>
                  {isConfirmed
                    ? `${checkLabel(f.check_type)}: ${t('difference')} ${inr(f.difference_paise)}`
                    : isUncertain
                      ? `${checkLabel(f.check_type)} ${t('needsReviewTitle')}`
                      : `${checkLabel(f.check_type)} — ${t('noIssueBadge')}`}
                </h3>
              </div>
              {!isInfo && (
                <div className="row">
                  <button
                    className={`btn btn-sm ${f.approved === true ? 'btn-primary' : 'btn-secondary'}`}
                    disabled={busyId === f.id || f.status !== 'confirmed'}
                    onClick={() => decide(f.id, true)}
                    aria-label={`${t('approve')} — ${checkLabel(f.check_type)}`}
                  >✓ {t('approve')}</button>
                  <button
                    className={`btn btn-sm ${f.approved === false ? 'btn-danger' : 'btn-ghost'}`}
                    disabled={busyId === f.id}
                    onClick={() => decide(f.id, false)}
                    aria-label={`${t('reject')} — ${checkLabel(f.check_type)}`}
                  >✕ {t('reject')}</button>
                </div>
              )}
            </div>

            <p className="finding-msg">{f.message}</p>
            {isInfo && <p className="muted" style={{ fontSize: 13, marginTop: -6, marginBottom: 14 }}>{t('underchargeNote')}</p>}

            <div className="amounts">
              <div className="amount-box"><div className="amount-label">{t('billed')}</div><div className="amount-val">{inr(f.billed_paise)}</div></div>
              <div className="amount-box"><div className="amount-label">{t('expected')}</div><div className="amount-val">{f.expected_paise != null ? inr(f.expected_paise) : t('notComputed')}</div></div>
              <div className="amount-box"><div className="amount-label">{t('difference')}</div><div className={`amount-val ${f.difference_paise > 0 ? 'red' : ''}`}>{f.difference_paise != null ? inr(f.difference_paise) : '—'}</div></div>
            </div>

            <div className="evidence">
              <div className="row space-between" style={{ marginBottom: 10 }}>
                <div className="evidence-title">{t('evidence')}</div>
                <button className="btn btn-ghost btn-xs" onClick={() => setViewQuote(q => ({ ...q, [f.id]: !quoteOpen }))}>
                  {quoteOpen ? '▾' : '▸'}
                </button>
              </div>
              <div className="evidence-grid">
                <div className="evidence-item"><div className="label">{t('document')}</div><div className="value">{ct.document_name || '—'}</div></div>
                <div className="evidence-item"><div className="label">{t('page')}</div><div className="value">{ct.page ? `${t('page')} ${ct.page}` : '—'}</div></div>
                <div className="evidence-item"><div className="label">{t('effectiveDate')}</div><div className="value">{ct.effective_date || '—'}</div></div>
                <div className="evidence-item"><div className="label">{t('statusReason')}</div><div className="value">{f.status}{f.reason ? ` (${f.reason})` : ''}</div></div>
              </div>
              {quoteOpen && ct.quote && (
                <div style={{ marginTop: 10 }}>
                  <div className="evidence-item"><div className="label">{t('clauseQuote')}</div></div>
                  <div className="quote">“{ct.quote}”</div>
                </div>
              )}
              {quoteOpen && f.calculation && (
                <div style={{ marginTop: 10 }}>
                  <div className="evidence-item"><div className="label">{t('calculation')}</div></div>
                  <div className="calc">{f.calculation}</div>
                </div>
              )}
            </div>

            {f.approved === true && <div className="badge badge-green">{t('inLetter')}</div>}
            {f.approved === false && <div className="badge badge-gray">{t('rejected')}</div>}
          </article>
        )
      })}

      <div className="card row space-between">
        <div>
          <div className="card-title" style={{ fontSize: 16 }}>{t('readyLetter')}</div>
          <div className="muted" style={{ fontSize: 14 }}>{t('readyLetterNote')}</div>
        </div>
        <button className="btn btn-primary" disabled={approvedCount === 0} onClick={() => nav(`/invoices/${invoiceId}/letter`)}>
          📝 {t('generateLetter')} ({approvedCount})
        </button>
      </div>
    </div>
  )
}
