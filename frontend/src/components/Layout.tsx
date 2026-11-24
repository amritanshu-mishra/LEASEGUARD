import { Link } from 'react-router-dom'
import { useLang } from '../i18nContext'

export function Layout({ children }: { children: React.ReactNode }) {
  const { lang, setLang, t } = useLang()
  return (
    <div className="app-shell">
      <div className="synthetic-banner">{t('syntheticBanner')}</div>
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">LG</span>
          <div>
            <div className="brand-name">{t('appName')}</div>
            <div className="brand-tag">{t('tagline')}</div>
          </div>
        </Link>
        <div className="topbar-actions">
          <div className="lang-toggle" aria-label="Language toggle">
            <button className={lang === 'en' ? 'chip active' : 'chip'} onClick={() => setLang('en')}>EN</button>
            <button className={lang === 'hi' ? 'chip active' : 'chip'} onClick={() => setLang('hi')}>हिंदी</button>
          </div>
        </div>
      </header>
      <main className="container">{children}</main>
      <footer className="footer">
        <span>{t('noSend')}</span>
        <span className="muted">{t('footerNote')}</span>
      </footer>
    </div>
  )
}
