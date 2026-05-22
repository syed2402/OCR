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

const LINE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#14b8a6',
]

// ---------------------------------------------------------------------------
// Rich custom tooltip
// ---------------------------------------------------------------------------
function CustomTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null

  // Grab judgement from the first payload's data point
  const judgement = (payload[0]?.payload as Record<string, unknown>)?.judgement as string | undefined
  const isNok = judgement?.toUpperCase() === 'NOK'

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl p-3 min-w-[160px]">
      <p className="text-gray-300 text-xs font-medium mb-2 border-b border-gray-700 pb-1.5">
        📅 {label}
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
            {entry.value ?? '—'}
          </span>
        </div>
      ))}
      {judgement && (
        <div
          className={`mt-2 pt-1.5 border-t border-gray-700 text-xs font-semibold text-center rounded ${
            isNok ? 'text-red-400' : 'text-green-400'
          }`}
        >
          {isNok ? '⚠ NOK' : '✓ OK'}
        </div>
      )}
    </div>
  )
}

export default function TrendChart({ rows }: Props) {
  const maxMeasurements = useMemo(
    () => Math.max(0, ...rows.map((r) => r.measurements.length)),
    [rows],
  )

  const chartData = useMemo(
    () =>
      rows.map((r) => {
        const point: Record<string, unknown> = {
          date: r.audit_date
            ? (() => {
                try { return format(parseISO(r.audit_date), 'dd MMM') }
                catch { return r.audit_date }
              })()
            : '—',
          judgement: r.judgement,
        }
        for (let i = 0; i < maxMeasurements; i++) {
          point[`M${i + 1}`] = r.measurements[i] ?? null
        }
        return point
      }),
    [rows, maxMeasurements],
  )

  if (rows.length === 0) {
    return <p className="text-center text-gray-400 py-8 text-sm">No data to chart</p>
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={chartData} margin={{ top: 12, right: 24, bottom: 8, left: 4 }}>
        {/* Lighter, less distracting grid */}
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
          tickFormatter={(v) => String(v)}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#e5e7eb', strokeWidth: 1.5, strokeDasharray: '4 4' }} />
        {maxMeasurements > 1 && (
          <Legend
            wrapperStyle={{ fontSize: 12, paddingTop: 12, color: '#6b7280' }}
            iconType="circle"
            iconSize={8}
          />
        )}
        {Array.from({ length: maxMeasurements }, (_, i) => (
          <Line
            key={`M${i + 1}`}
            type="monotone"
            dataKey={`M${i + 1}`}
            stroke={LINE_COLORS[i % LINE_COLORS.length]}
            strokeWidth={2.5}
            dot={{ r: 3.5, strokeWidth: 0, fill: LINE_COLORS[i % LINE_COLORS.length] }}
            activeDot={{ r: 6, strokeWidth: 2, stroke: '#fff', fill: LINE_COLORS[i % LINE_COLORS.length] }}
            connectNulls={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
