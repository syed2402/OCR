import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { FormEvent, useEffect, useState } from 'react'
import { Upload, ClipboardCheck, BarChart2, PanelLeftClose, PanelLeftOpen, LockKeyhole } from 'lucide-react'
import UploadPage from './pages/UploadPage'
import ReviewPage from './pages/ReviewPage'
import AnalyticsPage from './pages/AnalyticsPage'

function NavItem({
  to,
  icon: Icon,
  label,
  collapsed,
}: {
  to: string
  icon: React.ElementType
  label: string
  collapsed: boolean
}) {
  return (
    <NavLink
      to={to}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        `flex items-center rounded-lg text-sm font-medium transition-colors ${
          collapsed ? 'justify-center px-3 py-3' : 'gap-3 px-4 py-3'
        } ${
          isActive
            ? 'bg-blue-700 text-white'
            : 'text-blue-100 hover:bg-blue-800 hover:text-white'
        }`
      }
    >
      <Icon size={18} className="shrink-0" />
      <span
        className={`whitespace-nowrap transition-all duration-200 ${
          collapsed ? 'md:w-0 md:overflow-hidden md:opacity-0' : 'w-auto opacity-100'
        }`}
      >
        {label}
      </span>
    </NavLink>
  )
}

function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose

  return (
    <aside
      className={`bg-blue-900 flex shrink-0 transition-[width] duration-300 ease-in-out md:h-screen md:sticky md:top-0 md:flex-col ${
        collapsed ? 'md:w-16' : 'md:w-56'
      }`}
    >
      <div className={`hidden border-b border-blue-800 md:block ${collapsed ? 'px-3 py-4' : 'px-5 py-5'}`}>
        <div className="flex items-center justify-between gap-2">
          <div className={`min-w-0 transition-opacity duration-200 ${collapsed ? 'opacity-0 pointer-events-none w-0 overflow-hidden' : 'opacity-100'}`}>
            <p className="text-xs font-semibold text-blue-400 uppercase tracking-widest mb-1">
              Stellantis
            </p>
            <h1 className="text-white font-bold text-base leading-tight">
              Quality Analytics
            </h1>
          </div>
          <button
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-blue-100 hover:bg-blue-800 hover:text-white"
            onClick={() => setCollapsed((value) => !value)}
            type="button"
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <ToggleIcon size={18} />
          </button>
        </div>
      </div>
      <nav className="flex w-full gap-1 overflow-x-auto p-2 md:flex-1 md:flex-col md:p-3">
        <NavItem to="/" icon={Upload} label="Upload" collapsed={collapsed} />
        <NavItem to="/review" icon={ClipboardCheck} label="Review" collapsed={collapsed} />
        <NavItem to="/analytics" icon={BarChart2} label="Analytics" collapsed={collapsed} />
      </nav>
      <div className={`hidden border-t border-blue-800 py-4 md:block ${collapsed ? 'px-3' : 'px-5'}`}>
        <p
          className={`text-blue-400 text-xs whitespace-nowrap transition-all duration-200 ${
            collapsed ? 'w-0 overflow-hidden opacity-0' : 'opacity-100'
          }`}
        >
          Pilot v1.0
        </p>
      </div>
    </aside>
  )
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30000)
    return () => window.clearInterval(timer)
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!username.trim() || !password.trim()) {
      setError('Enter username and password')
      return
    }
    localStorage.setItem('ocr-authenticated', 'true')
    onLogin()
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-950 text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(37,99,235,0.35),transparent_30%),radial-gradient(circle_at_75%_45%,rgba(20,184,166,0.22),transparent_28%)]" />
      <div className="absolute inset-0 bg-slate-950/55" />

      <header className="relative z-10 flex items-start justify-between px-8 py-7">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-white/15 bg-white/10 text-sm font-bold">
            B
          </div>
          <div>
            <h1 className="text-base font-semibold leading-tight">BiztelAI technologies</h1>
            <p className="text-xs text-slate-300">Industrial AI Systems</p>
          </div>
        </div>
        <div className="text-right">
          <p className="text-3xl font-semibold tabular-nums">
            {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}
          </p>
          <p className="text-xs text-slate-300">
            {now.toLocaleDateString([], { weekday: 'long', day: '2-digit', month: 'short' })}
          </p>
        </div>
      </header>

      <main className="relative z-10 flex min-h-[calc(100vh-112px)] items-center justify-center px-6 pb-16">
        <form
          onSubmit={submit}
          className="w-full max-w-sm rounded-2xl border border-white/12 bg-white/10 p-7 shadow-2xl backdrop-blur-md"
        >
          <div className="mb-6 flex flex-col items-center text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-blue-600 shadow-lg shadow-blue-950/40">
              <LockKeyhole size={28} />
            </div>
            <h2 className="text-2xl font-semibold">Login</h2>
            <p className="mt-1 text-sm text-slate-300">Enter your credentials to continue</p>
          </div>

          <div className="space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-300">
                Username
              </span>
              <input
                className="h-11 w-full rounded-lg border border-white/15 bg-slate-950/45 px-3 text-sm text-white outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-500/30"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value)
                  setError('')
                }}
                autoComplete="username"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-300">
                Password
              </span>
              <input
                className="h-11 w-full rounded-lg border border-white/15 bg-slate-950/45 px-3 text-sm text-white outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-500/30"
                type="password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value)
                  setError('')
                }}
                autoComplete="current-password"
              />
            </label>
          </div>

          {error && <p className="mt-3 text-sm font-medium text-red-300">{error}</p>}

          <button
            className="mt-6 h-11 w-full rounded-lg bg-blue-600 text-sm font-semibold text-white transition hover:bg-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-slate-950"
            type="submit"
          >
            Sign in
          </button>
        </form>
      </main>
    </div>
  )
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(
    () => localStorage.getItem('ocr-authenticated') === 'true',
  )

  if (!authenticated) {
    return <LoginScreen onLogin={() => setAuthenticated(true)} />
  }

  return (
    <BrowserRouter>
      <div className="flex min-h-screen flex-col bg-gray-50 md:flex-row">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<UploadPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/review/:uploadId" element={<ReviewPage />} />
            <Route path="/analytics" element={<AnalyticsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
