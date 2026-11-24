import { Link, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import LeaseDetail from './pages/LeaseDetail'
import AuditPage from './pages/AuditPage'
import LetterPage from './pages/LetterPage'
import NewLeasePage from './pages/NewLeasePage'
import ActivityPage from './pages/ActivityPage'
import { Layout } from './components/Layout'
import { LangProvider, useLang } from './i18nContext'

function NotFound() {
  const { t } = useLang()
  return (
    <div className="empty">
      <div className="empty-icon" aria-hidden="true">🧭</div>
      <p className="empty-title">{t('notFoundTitle')}</p>
      <p className="empty-body">{t('notFoundBody')}</p>
      <Link className="btn btn-primary" to="/">{t('dashboard')}</Link>
    </div>
  )
}

export default function App() {
  return (
    <LangProvider>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/leases/new" element={<NewLeasePage />} />
          <Route path="/leases/:leaseId" element={<LeaseDetail />} />
          <Route path="/leases/:leaseId/audit/:invoiceId" element={<AuditPage />} />
          <Route path="/invoices/:invoiceId/letter" element={<LetterPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Layout>
    </LangProvider>
  )
}
