import { CheckCircle, XCircle, Hash, AlertTriangle, Gauge, Sigma } from 'lucide-react'
import { AnalyticsStats } from '../api/client'

interface Props {
  stats: AnalyticsStats
}

interface CardProps {
  icon: React.ReactNode
  value: string
  label: string
  sub: string
  accent: 'green' | 'red' | 'blue' | 'amber' | 'purple'
}

const ACCENT = {
  green:  { bg: 'bg-green-50',  icon: 'bg-green-100',  val: 'text-green-700',  border: 'border-green-200' },
  red:    { bg: 'bg-red-50',    icon: 'bg-red-100',    val: 'text-red-600',    border: 'border-red-200' },
  blue:   { bg: 'bg-blue-50',   icon: 'bg-blue-100',   val: 'text-blue-700',   border: 'border-blue-200' },
  amber:  { bg: 'bg-amber-50',  icon: 'bg-amber-100',  val: 'text-amber-700',  border: 'border-amber-200' },
  purple: { bg: 'bg-purple-50', icon: 'bg-purple-100', val: 'text-purple-700', border: 'border-purple-200' },
}

function Card({ icon, value, label, sub, accent }: CardProps) {
  const c = ACCENT[accent]
  return (
    <div className={`min-w-0 rounded-xl border ${c.border} ${c.bg} p-4 flex items-center gap-3`}>
      <div className={`h-10 w-10 ${c.icon} rounded-xl flex items-center justify-center shrink-0`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className={`truncate text-2xl font-bold ${c.val} leading-tight`}>{value}</p>
        <p className="mt-0.5 break-words text-sm leading-snug text-gray-600">{label}</p>
        <p className="mt-0.5 break-words text-xs leading-snug text-gray-400">{sub}</p>
      </div>
    </div>
  )
}

export default function OkNokSummary({ stats }: Props) {
  const { total, ok_count, nok_count, ok_pct, nok_pct, avg_torque, cp, cpk } = stats

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
      <Card
        accent="blue"
        icon={<Hash className="text-blue-600" size={20} />}
        value={String(total)}
        label="Total Records"
        sub="approved data only"
      />
      <Card
        accent="green"
        icon={<CheckCircle className="text-green-600" size={20} />}
        value={`${ok_pct}%`}
        label="OK Rate"
        sub={`${ok_count} records`}
      />
      <Card
        accent="red"
        icon={<XCircle className="text-red-500" size={20} />}
        value={`${nok_pct}%`}
        label="NG Rate"
        sub={`${nok_count} records`}
      />
      <Card
        accent="amber"
        icon={<AlertTriangle className="text-amber-600" size={20} />}
        value={String(nok_count)}
        label="NG Count"
        sub="failures detected"
      />
      <Card
        accent="purple"
        icon={<Gauge className="text-purple-600" size={20} />}
        value={avg_torque != null ? String(avg_torque) : '—'}
        label="Avg Torque"
        sub="across all measurements"
      />
      <Card
        accent="blue"
        icon={<Sigma className="text-blue-600" size={20} />}
        value={cp != null ? String(cp) : '—'}
        label="Cp"
        sub="process capability"
      />
      <Card
        accent="green"
        icon={<Sigma className="text-green-600" size={20} />}
        value={cpk != null ? String(cpk) : '—'}
        label="Cpk"
        sub="centered capability"
      />
    </div>
  )
}
