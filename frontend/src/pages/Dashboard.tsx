import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'

export default function Dashboard() {
  const { t } = useLang()
  const nav = useNavigate()
  const [leases, setLeases] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true); setErr('')
    try { const d = await api.listLeases(); setLeases(d.leases || []) }
    catch (e: any) { setErr(e.message || 'Failed to load') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  const loadSample = async () => {
    setBusy(true); setErr('')
    try {
      const d = await api.loadSample()
      await load()
      if (d.lease_id) nav(`/leases/${d.lease_id}`)
    } catch (e: any) { setErr(e.message || 'Failed to load sample') }
    finally { setBusy(false) }
  }

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">{t('yourLeases')}</h1>
          <p className="page-sub">{t('dashboardSub')}</p>
        </div>
        <div className="row">
          <Link className="btn btn-secondary" to="/leases/new">＋ {t('newLease')}</Link>
          <button className="btn btn-primary" onClick={loadSample} disabled={busy}>
            {busy ? t('loading') : `📄 ${t('loadSample')}`}
          </button>
        </div>
      </div>

      {err && <div className="error" style={{ marginBottom: 16 }}>{err}</div>}

      <div className="grid grid-3" style={{ marginBottom: 20 }}>
        <div className="stat"><div className="stat-label">{t('step')} 1</div><div className="step-title">{t('step1Title')}</div><div className="stat-note">{t('step1Note')}</div></div>
        <div className="stat"><div className="stat-label">{t('step')} 2</div><div className="step-title">{t('step2Title')}</div><div className="stat-note">{t('step2Note')}</div></div>
        <div className="stat"><div className="stat-label">{t('step')} 3</div><div className="step-title">{t('step3Title')}</div><div className="stat-note">{t('step3Note')}</div></div>
      </div>

      {loading ? <div className="loading">{t('loading')}</div> : leases.length === 0 ? (
        <div className="empty">
          <p className="empty-title">{t('noLeases')}</p>
          <p style={{ marginBottom: 16 }}>{t('noLeasesHint')}</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn btn-primary" onClick={loadSample} disabled={busy}>{t('loadSample')}</button>
            <Link className="btn btn-secondary" to="/leases/new">{t('newLease')}</Link>
          </div>
        </div>
      ) : (
        <div className="grid grid-2">
          {leases.map(l => (
            <div key={l.id} className="card">
              <div className="row space-between" style={{ marginBottom: 8 }}>
                <div className="card-title">{l.property_label}</div>
                {l.pending_findings > 0
                  ? <span className="badge badge-amber">{t('reviewNeeded')}</span>
                  : <span className="badge badge-green">{t('ready')}</span>}
              </div>
              <div className="muted" style={{ fontSize: 14, marginBottom: 12 }}>{l.start_date} → {l.end_date}</div>
              <div className="row" style={{ gap: 8, marginBottom: 16 }}>
                <span className="badge badge-gray">{l.documents_count} {t('docs')}</span>
                <span className="badge badge-gray">{l.invoices_count} {t('invoices')}</span>
                {l.pending_findings > 0 && <span className="badge badge-red">{l.pending_findings} {t('pending')}</span>}
              </div>
              <div className="row">
                <Link className="btn btn-primary btn-sm" to={`/leases/${l.id}`}>{t('open')} →</Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
