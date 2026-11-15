import { lazy, Suspense } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const UploadPage = lazy(() => import('./pages/UploadPage'))
const AnalysisPage = lazy(() => import('./pages/AnalysisPage'))
const EventsPage = lazy(() => import('./pages/EventsPage'))
const RealtimePage = lazy(() => import('./pages/RealtimePage'))
const AuditPage = lazy(() => import('./pages/AuditPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))

export default function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark">LF</span>
          <span className="brand-text">
            低频噪音平台
            <small>离线分析 / 实时测量</small>
          </span>
        </Link>
        <nav aria-label="主导航">
          <span className="nav-label">工作台</span>
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            总览
          </NavLink>
          <NavLink to="/upload" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            测量分析
          </NavLink>
          <NavLink to="/events" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            噪音事件
          </NavLink>
          <NavLink to="/realtime" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            实时测量
          </NavLink>
          <span className="nav-label">系统</span>
          <NavLink to="/audit" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            审计日志
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            隐私与校准
          </NavLink>
        </nav>
        <p className="sidebar-note">未校准麦克风的读数仅作相对排查参考，不作为法定声级。</p>
      </aside>

      <main className="main" id="main">
        <Suspense fallback={<p className="empty">页面加载中…</p>}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/analyses/:runId" element={<AnalysisPage />} />
            <Route path="/events" element={<EventsPage />} />
            <Route path="/realtime" element={<RealtimePage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<p className="empty">页面不存在</p>} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
