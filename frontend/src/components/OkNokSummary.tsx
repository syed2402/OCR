import { XCircle, Hash, AlertTriangle, Gauge, ArrowDown, ArrowUp, ChevronsDown, ChevronsUp } from 'lucide-react'
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
  green: { bg: 'bg-green-50', icon: 'bg-green-100', val: 'text-green-700', border: 'border-green-200' },
  red: { bg: 'bg-red-50', icon: 'bg-red-100', val: 'text-red-600', border: 'border-red-200' },
  blue: { bg: 'bg-blue-50', icon: 'bg-blue-100', val: 'text-blue-700', border: 'border-blue-200' },
  amber: { bg: 'bg-amber-50', icon: 'bg-amber-100', val: 'text-amber-700', border: 'border-amber-200' },
  purple: { bg: 'bg-purple-50', icon: 'bg-purple-100', val: 'text-purple-700', border: 'border-purple-200' },
}

function Card({ icon, value, label, sub, accent }: CardProps) {
  const c = ACCENT[accent]
  return (
    <div className={`min-w-[168px] rounded-lg border ${c.border} ${c.bg} px-4 py-3 flex items-center gap-3`}>
      <div className={`h-9 w-9 ${c.icon} rounded-lg flex items-center justify-center shrink-0`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className={`text-xl font-bold ${c.val} leading-tight`}>{value}</p>
        <p className="mt-0.5 whitespace-nowrap text-sm leading-snug text-gray-700">{label}</p>
        <p className="mt-0.5 whitespace-nowrap text-xs leading-snug text-gray-400">{sub}</p>
      </div>
    </div>
  )
}

export default function OkNokSummary({ stats }: Props) {
  const {
    total,
    ok_count,
    nok_count,
    nok_pct,
    avg_torque,
    min_torque,
    max_torque,
    lower_proximity_count,
    upper_proximity_count,
  } = stats
  const measurementCount = ok_count + nok_count
  const countLabel = avg_torque != null ? 'measurements' : 'records'

  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      <Card
        accent="blue"
        icon={<Hash className="text-blue-600" size={20} />}
        value={String(measurementCount || total)}
        label="Measurements"
        sub="approved only"
      />
      <Card
        accent="red"
        icon={<XCircle className="text-red-500" size={20} />}
        value={`${nok_pct}%`}
        label="NG Rate"
        sub={`${nok_count} ${countLabel}`}
      />
      <Card
        accent="amber"
        icon={<AlertTriangle className="text-amber-600" size={20} />}
        value={String(nok_count)}
        label="NG Count"
        sub={countLabel}
      />
      <Card
        accent="purple"
        icon={<Gauge className="text-purple-600" size={20} />}
        value={avg_torque != null ? String(avg_torque) : '-'}
        label="Average"
        sub="torque"
      />
      <Card
        accent="blue"
        icon={<ArrowDown className="text-blue-600" size={20} />}
        value={min_torque != null ? String(min_torque) : '-'}
        label="Minimum"
        sub="torque"
      />
      <Card
        accent="green"
        icon={<ArrowUp className="text-green-600" size={20} />}
        value={max_torque != null ? String(max_torque) : '-'}
        label="Maximum"
        sub="torque"
      />
      <Card
        accent="blue"
        icon={<ChevronsDown className="text-blue-600" size={20} />}
        value={String(lower_proximity_count ?? 0)}
        label="Lower Zone"
        sub="bottom 10%"
      />
      <Card
        accent="amber"
        icon={<ChevronsUp className="text-amber-600" size={20} />}
        value={String(upper_proximity_count ?? 0)}
        label="Upper Zone"
        sub="top 10%"
      />
    </div>
  )
}
