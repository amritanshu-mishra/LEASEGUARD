import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'

type Lease = {
  id: string
  property_label: string
  start_date: string
  end_date: string
  documents_count: number
  invoices_count: number
  pending_findings: number
}

export default function Dashboard() {
  const { t } = useLang()
  const nav = useNavigate()
  const [leases, setLeases] = useState<Lease[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setErr('')
    try {
      const d = await api.listLeases()
      setLeases(d.leases || [])
    } catch (e: any) {
      setErr(e.message || t('loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const loadSample = async () => {
    setBusy(true); setErr('')
    try {
      const d = await api.loadSample()
      await load()
      if (d.lease_id) nav(`/leases/${d.lease_id}`)
    } catch (e: any) {
      setErr(e.message || t('loadFailed'))
    } finally {
      setBusy(false)
    }
  }

  const activeCount = leases.length
  const needsReview = leases.filter(l => l.pending_findings > 0).length

  return (
    <div>
      <div className="hero">
        <div>
          <h1 className="page-title">{t('yourLeases')}</h1>
          <p className="page-sub" style={{ marginBottom: 0 }}>{t('dashboardSub')}</p>
        </div>
        <div className="row">
          <Link className="btn btn-primary" to="/leases/new">＋ {t('newLease')}</Link>
          <button className="btn btn-secondary" onClick={loadSample} disabled={busy}>
            {busy ? t('loading') : `📄 ${t('loadSample')}`}
          </button>
        </div>
      </div>

      {err && (
        <div className="error" role="alert" style={{ marginBottom: 16 }}>
          {err}
          <button className="btn btn-ghost btn-sm" style={{ marginLeft: 12 }} onClick={load}>{t('retry')}</button>
        </div>
      )}

      {!loading && leases.length > 0 && (
        <div className="summary-strip">
          <span><strong>{activeCount}</strong> {t('leasesTracked')}</span>
          <span className={needsReview > 0 ? 'warn' : ''}>
            <strong>{needsReview}</strong> {t('needReview')}
          </span>
          <span className="muted">{t('summaryHint')}</span>
        </div>
      )}

      {loading ? (
        <div className="loading" role="status">{t('loading')}</div>
      ) : leases.length === 0 ? (
        <div className="empty">
          <div className="empty-icon" aria-hidden="true">🧾</div>
          <p className="empty-title">{t('noLeases')}</p>
          <p className="empty-body">{t('noLeasesHint')}</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn btn-primary" onClick={loadSample} disabled={busy}>{t('loadSample')}</button>
            <Link className="btn btn-secondary" to="/leases/new">{t('newLease')}</Link>
          </div>
          <p className="empty-foot muted">{t('sampleIsSynthetic')}</p>
        </div>
      ) : (
        <div className="grid grid-2">
          {leases.map(l => (
            <article key={l.id} className="card lease-card">
              <div className="row space-between" style={{ marginBottom: 8 }}>
                <h2 className="card-title">{l.property_label}</h2>
                {l.pending_findings > 0
                  ? <span className="badge badge-amber">! {t('reviewNeeded')}</span>
                  : <span className="badge badge-green">✓ {t('upToDate')}</span>}
              </div>
              <div className="muted" style={{ fontSize: 14, marginBottom: 12 }}>
                {l.start_date} → {l.end_date}
              </div>
              <div className="row" style={{ gap: 8, marginBottom: 16 }}>
                <span className="badge badge-gray">{l.documents_count} {t('docs')}</span>
                <span className="badge badge-gray">{l.invoices_count} {t('invoices')}</span>
                {l.pending_findings > 0 && (
                  <span className="badge badge-red">{l.pending_findings} {t('pending')}</span>
                )}
              </div>
              <div className="row">
                <Link className="btn btn-primary btn-sm" to={`/leases/${l.id}`}>
                  {t('open')} →
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}

      <div className="how-it-works">
        <h2 className="section-title">{t('howItWorks')}</h2>
        <ol className="how-steps">
          <li>
            <span className="how-num">1</span>
            <div><strong>{t('step1Title')}</strong><p>{t('step1Note')}</p></div>
          </li>
          <li>
            <span className="how-num">2</span>
            <div><strong>{t('step2Title')}</strong><p>{t('step2Note')}</p></div>
          </li>
          <li>
            <span className="how-num">3</span>
            <div><strong>{t('step3Title')}</strong><p>{t('step3Note')}</p></div>
          </li>
        </ol>
        <p className="how-foot muted">{t('noRecoveryClaim')}</p>
      </div>
    </div>
  )
}
