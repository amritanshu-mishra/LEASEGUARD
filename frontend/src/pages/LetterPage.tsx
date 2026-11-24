import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { normaliseInr } from '../format'
import { useLang } from '../i18nContext'

export default function LetterPage() {
  const { invoiceId } = useParams()
  const { t } = useLang()
  const [letter, setLetter] = useState<any>(null)
  const [invoice, setInvoice] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [copied, setCopied] = useState(false)
  const [letterLang, setLetterLang] = useState<'en' | 'hi'>('en')
  const [letterLoading, setLetterLoading] = useState(false)

  useEffect(() => {
    api.getFindings(invoiceId!)
      .then(fd => { if (fd?.invoice) setInvoice(fd.invoice) })
      .catch(() => null)
  }, [invoiceId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true); setLetterLoading(true); setErr('')
      try {
        const l = await api.generateLetter(invoiceId!, letterLang)
        if (!cancelled) setLetter(l)
      } catch (e: any) {
        if (!cancelled) setErr(e.message || 'Failed to generate letter')
      } finally {
        if (!cancelled) { setLoading(false); setLetterLoading(false) }
      }
    })()
    return () => { cancelled = true }
  }, [invoiceId, letterLang])

  const copyText = async () => {
    if (!letter?.text) return
    try {
      await navigator.clipboard.writeText(letter.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setErr(t('copiedFail'))
    }
  }

  const shareWhatsApp = () => {
    if (!letter?.text) return
    // wa.me opens WhatsApp with the letter pre-filled; the user still presses send.
    window.open(`https://wa.me/?text=${encodeURIComponent(letter.text)}`, '_blank', 'noopener,noreferrer')
  }

  if (loading && !letter) return <div className="loading">{t('preparingLetter')}</div>
  if (err && !letter) return <div className="error">{err}</div>
  if (!letter) return <div className="error">{t('noLetter')}</div>

  const backLink = invoice?.lease_id ? `/leases/${invoice.lease_id}/audit/${invoiceId}` : '/'

  return (
    <div>
      <div className="row space-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 className="page-title">{t('letterTitle')}</h1>
          <p className="page-sub">{t('letterSub')}</p>
        </div>
        <Link to={backLink} className="btn btn-ghost btn-sm">← {t('backToFindings')}</Link>
      </div>

      {err && <div className="error" style={{ marginBottom: 16 }}>{err}</div>}

      <div className="stat-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
        <div className="stat">
          <div className="stat-label">{t('approvedFindings')}</div>
          <div className="stat-value">{letter.approved_count}</div>
          <div className="stat-note">{t('inThisLetter')}</div>
        </div>
        <div className="stat">
          <div className="stat-label">{t('totalFlagged')}</div>
          <div className="stat-value red">{normaliseInr(letter.total_flagged_display)}</div>
          <div className="stat-note">{t('notClaim')}</div>
        </div>
        <div className="stat">
          <div className="stat-label">{t('shareChannel')}</div>
          <div className="stat-value" style={{ fontSize: 20 }}>WhatsApp</div>
          <div className="stat-note">{t('primaryAction')}</div>
        </div>
      </div>

      <div className="notice" style={{ marginBottom: 16 }}>
        <strong>{t('notLegal')}</strong> {t('verifyBeforeSending')}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row space-between" style={{ marginBottom: 12 }}>
          <h2 className="section-title" style={{ margin: 0 }}>{t('letterText')}</h2>
          <div className="row">
            <button className="btn btn-whatsapp" onClick={shareWhatsApp}>🟢 {t('shareWhatsApp')}</button>
            <button className="btn btn-secondary" onClick={copyText}>{copied ? `✓ ${t('copied')}` : `📋 ${t('copyText')}`}</button>
          </div>
        </div>

        <div className="row" style={{ marginBottom: 12, gap: 10 }}>
          <span className="muted" style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.04em' }}>
            {t('letterLanguage')}
          </span>
          <div className="lang-toggle">
            <button className={letterLang === 'en' ? 'chip active' : 'chip'} onClick={() => setLetterLang('en')} disabled={letterLoading}>
              {t('letterEn')}
            </button>
            <button className={letterLang === 'hi' ? 'chip active' : 'chip'} onClick={() => setLetterLang('hi')} disabled={letterLoading}>
              {t('letterHi')}
            </button>
          </div>
          {letterLoading && <span className="badge badge-gray">{t('loading')}</span>}
        </div>

        <div className="letter-box" lang={letter.language === 'hi' ? 'hi' : 'en'}>{letter.text}</div>
        <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>{t('letterLangNote')}</p>
      </div>

      <div className="info">
        <strong>{t('whyWhatsApp')}</strong> {t('whyWhatsAppText')}
      </div>
    </div>
  )
}
