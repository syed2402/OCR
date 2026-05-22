import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { useState } from 'react'
import { Upload, ClipboardCheck, BarChart2, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
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
          collapsed ? 'w-0 overflow-hidden opacity-0' : 'w-auto opacity-100'
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
      className={`bg-blue-900 flex flex-col shrink-0 h-screen sticky top-0 transition-[width] duration-300 ease-in-out ${
        collapsed ? 'w-16' : 'w-56'
      }`}
    >
      <div className={`border-b border-blue-800 ${collapsed ? 'px-3 py-4' : 'px-5 py-5'}`}>
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
      <nav className="flex-1 p-3 flex flex-col gap-1">
        <NavItem to="/" icon={Upload} label="Upload" collapsed={collapsed} />
        <NavItem to="/review" icon={ClipboardCheck} label="Review" collapsed={collapsed} />
        <NavItem to="/analytics" icon={BarChart2} label="Analytics" collapsed={collapsed} />
      </nav>
      <div className={`border-t border-blue-800 py-4 ${collapsed ? 'px-3' : 'px-5'}`}>
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

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen bg-gray-50">
        <Sidebar />
        <main className="flex-1 overflow-auto">
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
