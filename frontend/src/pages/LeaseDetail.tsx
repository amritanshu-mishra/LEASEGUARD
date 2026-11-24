import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'

function fmtPaise(p: number | null | undefined) {
  if (p == null) return '—'
  return `₹${(p / 100).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

export default function LeaseDetail() {
  const { leaseId } = useParams()
  const { t } = useLang()
  const [lease, setLease] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    (async () => {
      setLoading(true); setErr('')
      try { setLease(await api.getLease(leaseId!)) }
      catch (e: any) { setErr(e.message || 'Failed to load lease') }
      finally { setLoading(false) }
    })()
  }, [leaseId])

  const termLabel = (tt: string) =>
    tt === 'base_rent_paise' ? t('baseRent')
      : tt === 'escalation_rate_bp' ? t('escalation')
      : tt === 'maintenance_cap_paise' ? t('maintenanceCap')
      : tt

  const fmtTermValue = (tt: string, v: number | null | undefined) => {
    if (v == null) return '—'
    if (tt === 'escalation_rate_bp') return `${(v / 100).toFixed(2)}% ${t('perYear')}`
    if (tt === 'base_rent_paise' || tt === 'maintenance_cap_paise') return fmtPaise(v)
    return String(v)
  }

  if (loading) return <div className="loading">{t('loadingLease')}</div>
  if (err) return <div className="error">{err}</div>
  if (!lease) return <div className="error">{t('leaseNotFound')}</div>

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">{lease.property_label}</h1>
          <p className="page-sub">{t('leasePeriod')}: {lease.start_date} → {lease.end_date}</p>
        </div>
        <Link to="/" className="btn btn-ghost btn-sm">← {t('dashboard')}</Link>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2 className="section-title">{t('documents')}</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('name')}</th><th>{t('type')}</th><th>{t('effectiveDate')}</th></tr></thead>
              <tbody>
                {lease.documents.map((d: any) => (
                  <tr key={d.id}>
                    <td>{d.name}</td>
                    <td><span className="badge badge-gray">{d.kind}</span></td>
                    <td>{d.effective_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <h2 className="section-title">{t('termsInForce')} {lease.as_of}</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('term')}</th><th>{t('value')}</th><th>{t('source')}</th></tr></thead>
              <tbody>
                {lease.terms_in_force.map((row: any) => (
                  <tr key={row.term_type}>
                    <td>{termLabel(row.term_type)}</td>
                    <td>{row.status === 'resolved'
                      ? fmtTermValue(row.term_type, row.value)
                      : <span className="badge badge-amber">{t('uncertain')}</span>}</td>
                    <td style={{ fontSize: 13 }}>
                      {row.status === 'resolved'
                        ? <>{row.document_name}, p.{row.page} <span className="badge badge-teal" style={{ marginLeft: 4 }}>{t('controls')}</span></>
                        : (row.reason || '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {lease.superseded_terms?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 11, fontWeight: 700, marginBottom: 6 }}>{t('superseded')}</div>
              {lease.superseded_terms.map((s: any, i: number) => (
                <div key={i} style={{ fontSize: 13, color: 'var(--muted)', textDecoration: 'line-through', marginBottom: 4 }}>
                  {termLabel(s.term_type)}: {fmtTermValue(s.term_type, s.value)} ({s.document_name}, p.{s.page}, {s.effective_date})
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2 className="section-title">{t('invoices')}</h2>
        {lease.invoices.length === 0 ? <div className="muted">{t('noInvoices')}</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t('period')}</th><th>{t('items')}</th><th>{t('status')}</th><th></th></tr></thead>
              <tbody>
                {lease.invoices.map((inv: any) => (
                  <tr key={inv.id}>
                    <td style={{ fontWeight: 600 }}>{inv.period_label || inv.period_start}</td>
                    <td>{inv.line_items?.map((li: any) => `${li.description}: ${fmtPaise(li.amount_paise)}`).join(' • ')}</td>
                    <td>
                      {inv.findings_count > 0 ? (
                        <span className="row" style={{ gap: 6 }}>
                          {inv.confirmed_count > 0 && <span className="badge badge-red">{inv.confirmed_count} {t('discrepancy')}</span>}
                          {inv.uncertain_count > 0 && <span className="badge badge-amber">{inv.uncertain_count} {t('uncertain')}</span>}
                          {inv.confirmed_count === 0 && inv.uncertain_count === 0 && <span className="badge badge-green">{t('noIssues')}</span>}
                        </span>
                      ) : <span className="badge badge-gray">{t('notAudited')}</span>}
                    </td>
                    <td className="num">
                      <Link className="btn btn-primary btn-sm" to={`/leases/${lease.id}/audit/${inv.id}`}>🔍 {t('auditInvoice')}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
