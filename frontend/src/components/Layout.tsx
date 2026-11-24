import { Link } from 'react-router-dom'
import { useLang } from '../i18nContext'

export function Layout({ children }: { children: React.ReactNode }) {
  const { lang, setLang, t } = useLang()
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <div className="synthetic-banner" role="note" aria-label={t('syntheticBanner')}>
        {t('syntheticBanner')}
      </div>
      <header className="topbar">
        <Link to="/" className="brand" aria-label={`${t('appName')} — ${t('tagline')}`}>
          <span className="brand-mark">LG</span>
          <div>
            <div className="brand-name">{t('appName')}</div>
            <div className="brand-tag">{t('tagline')}</div>
          </div>
        </Link>
        <div className="topbar-actions">
          <Link className="topbar-link" to="/activity">{t('activity')}</Link>
          <div className="lang-toggle" role="group" aria-label="Choose language / भाषा चुनें">
            <button
              type="button"
              className={lang === 'en' ? 'chip active' : 'chip'}
              onClick={() => setLang('en')}
              aria-pressed={lang === 'en'}
              lang="en"
            >EN</button>
            <button
              type="button"
              className={lang === 'hi' ? 'chip active' : 'chip'}
              onClick={() => setLang('hi')}
              aria-pressed={lang === 'hi'}
              lang="hi"
            >हिंदी</button>
          </div>
        </div>
      </header>
      <main id="main-content" className="container" tabIndex={-1}>{children}</main>
      <footer className="footer" role="contentinfo">
        <span>{t('noSend')}</span>
        <span className="muted">{t('footerNote')}</span>
      </footer>
    </div>
  )
}
