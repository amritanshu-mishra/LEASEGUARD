import { Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import LeaseDetail from './pages/LeaseDetail'
import AuditPage from './pages/AuditPage'
import LetterPage from './pages/LetterPage'
import NewLeasePage from './pages/NewLeasePage'
import { Layout } from './components/Layout'
import { LangProvider } from './i18nContext'

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
        </Routes>
      </Layout>
    </LangProvider>
  )
}
