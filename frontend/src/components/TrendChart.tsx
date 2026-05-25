import { useMemo } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  TooltipProps,
} from 'recharts'
import { format, parseISO } from 'date-fns'
import { AnalyticsRow } from '../api/client'

interface Props {
  rows: AnalyticsRow[]
}

function CustomTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null

  const point = payload[0]?.payload as Record<string, unknown>

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg shadow-2xl p-3 min-w-[180px]">
      <p className="text-gray-300 text-xs font-medium mb-2 border-b border-gray-700 pb-1.5">
        {label}
      </p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5 text-xs text-gray-400">
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            {entry.name}
          </span>
          <span className="text-xs font-mono font-semibold text-white">
            {entry.value ?? '-'}
          </span>
        </div>
      ))}
      {typeof point.count === 'number' && (
        <p className="mt-2 border-t border-gray-700 pt-1.5 text-xs text-gray-400">
          {point.count} measurements
        </p>
      )}
    </div>
  )
}

export default function TrendChart({ rows }: Props) {
  const chartData = useMemo(() => {
    const byDate = new Map<string, {
      values: number[]
      lower: number | null
      upper: number | null
    }>()

    rows.forEach((row) => {
      const key = row.audit_date ?? 'Unknown'
      const bucket = byDate.get(key) ?? { values: [], lower: null, upper: null }
      bucket.values.push(...row.measurements.filter((value): value is number => typeof value === 'number'))
      bucket.lower = bucket.lower ?? row.lower_limit ?? null
      bucket.upper = bucket.upper ?? row.upper_limit ?? null
      byDate.set(key, bucket)
    })

    return Array.from(byDate.entries())
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([rawDate, bucket]) => {
        const avg = bucket.values.length
          ? bucket.values.reduce((sum, value) => sum + value, 0) / bucket.values.length
          : null
        const date = rawDate === 'Unknown'
          ? rawDate
          : (() => {
              try { return format(parseISO(rawDate), 'dd MMM') }
              catch { return rawDate }
            })()

        return {
          date,
          avg: avg === null ? null : Number(avg.toFixed(2)),
          lower: bucket.lower,
          upper: bucket.upper,
          count: bucket.values.length,
        }
      })
  }, [rows])

  if (rows.length === 0) {
    return <p className="text-center text-gray-400 py-8 text-sm">No data to chart</p>
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={chartData} margin={{ top: 12, right: 24, bottom: 8, left: 4 }}>
        <CartesianGrid strokeDasharray="4 4" stroke="#f3f4f6" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: '#9ca3af' }}
          tickLine={false}
          axisLine={false}
          dy={6}
        />
        <YAxis
          tick={{ fontSize: 11, fill: '#9ca3af' }}
          tickLine={false}
          axisLine={false}
          width={44}
          tickFormatter={(value) => String(value)}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#e5e7eb', strokeWidth: 1.5, strokeDasharray: '4 4' }} />
        <Legend
          wrapperStyle={{ fontSize: 12, paddingTop: 12, color: '#6b7280' }}
          iconType="circle"
          iconSize={8}
        />
        <Line
          type="monotone"
          name="Avg"
          dataKey="avg"
          stroke="#2563eb"
          strokeWidth={2.5}
          dot={{ r: 4, strokeWidth: 0, fill: '#2563eb' }}
          activeDot={{ r: 6, strokeWidth: 2, stroke: '#fff', fill: '#2563eb' }}
          connectNulls={false}
        />
        <Line
          type="monotone"
          name="Upper Limit"
          dataKey="upper"
          stroke="#dc2626"
          strokeDasharray="6 4"
          strokeWidth={2}
          dot={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          name="Lower Limit"
          dataKey="lower"
          stroke="#16a34a"
          strokeDasharray="6 4"
          strokeWidth={2}
          dot={false}
          connectNulls={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
