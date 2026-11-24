import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLang } from '../i18nContext'

type Event = {
  id: string
  action: string
  entity: string
  entity_id: string
  detail: string
  created_at: string
}

const ACTIONS: Record<string, { en: string; hi: string; icon: string }> = {
  sample_loaded: { en: 'Demo case loaded', hi: 'डेमो केस लोड हुआ', icon: '📄' },
  lease_created: { en: 'Lease created', hi: 'लीज़ बनाई गई', icon: '🏪' },
  document_uploaded: { en: 'Document uploaded', hi: 'दस्तावेज़ अपलोड हुआ', icon: '⬆️' },
  audit_run: { en: 'Charges audited', hi: 'शुल्कों की जांच हुई', icon: '🔍' },
  finding_decision: { en: 'Discrepancy marked', hi: 'विसंगति चिह्नित', icon: '✓' },
  letter_generated: { en: 'Letter generated', hi: 'पत्र तैयार हुआ', icon: '📝' },
  lease_deleted: { en: 'Lease deleted', hi: 'लीज़ हटाई गई', icon: '🗑' },
}

function when(iso: string, lang: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(lang === 'hi' ? 'hi-IN' : 'en-IN', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export default function ActivityPage() {
  const { t, lang } = useLang()
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.listAuditLog()
      .then(d => setEvents(d.events || []))
      .catch((e: any) => setErr(e.message || t('loadFailed')))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <Link className="backlink" to="/">← {t('dashboard')}</Link>
      <h1 className="page-title">{t('activityTitle')}</h1>
      <p className="page-sub">{t('activitySub')}</p>

      {err && <div className="error" role="alert">{err}</div>}
      {loading ? (
        <div className="loading" role="status">{t('loading')}</div>
      ) : events.length === 0 ? (
        <div className="empty">{t('activityEmpty')}</div>
      ) : (
        <ol className="timeline">
          {events.map(e => {
            let meta = ACTIONS[e.action] || { en: e.action.replace(/_/g, ' '), hi: e.action.replace(/_/g, ' '), icon: '•' }
            if (e.action === 'finding_decision' && e.detail === 'rejected') {
              meta = { en: 'Discrepancy rejected', hi: 'विसंगति अस्वीकृत', icon: '✕' }
            } else if (e.action === 'finding_decision') {
              meta = { en: 'Discrepancy approved', hi: 'विसंगति स्वीकृत', icon: '✓' }
            }
            return (
              <li key={e.id} className="timeline-item">
                <span className="timeline-icon" aria-hidden="true">{meta.icon}</span>
                <div>
                  <div className="timeline-title">{lang === 'hi' ? meta.hi : meta.en}</div>
                  <div className="timeline-meta">
                    <time dateTime={e.created_at}>{when(e.created_at, lang)}</time>
                    <span className="muted"> · {e.entity}</span>
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      )}

      <p className="how-foot muted" style={{ marginTop: 20 }}>{t('activityPrivacy')}</p>
    </div>
  )
}
