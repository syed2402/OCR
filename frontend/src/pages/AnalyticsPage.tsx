/**
 * Screen 3 — Analytics Explorer
 *
 * Left sidebar: searchable operation list (APPROVED data only).
 * Main area: date filter → measurements table + trend chart + OK/NOK stats.
 *
 * ALL data shown here comes from review_status = 'APPROVED' rows only.
 * This is enforced on the server; the frontend never overrides it.
 */

import { useEffect, useMemo, useState } from 'react'
import { format, subDays, parseISO } from 'date-fns'
import { Search, BarChart2, RefreshCw, Clock, Database, ArrowUpDown } from 'lucide-react'
import toast from 'react-hot-toast'
import { getAnalytics, listOperations, AnalyticsResponse, Operation } from '../api/client'
import MeasurementsTable from '../components/MeasurementsTable'
import TrendChart from '../components/TrendChart'
import OkNokSummary from '../components/OkNokSummary'

type DateRange = '7d' | '30d' | 'custom'
type SortMode = 'alpha' | 'records' | 'recent' | 'nok'

const today = () => format(new Date(), 'yyyy-MM-dd')
const daysAgo = (n: number) => format(subDays(new Date(), n), 'yyyy-MM-dd')

export default function AnalyticsPage() {
  const [operations, setOperations] = useState<Operation[]>([])
  const [search, setSearch] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>('alpha')
  const [selectedOp, setSelectedOp] = useState<Operation | null>(null)

  const [dateRange, setDateRange] = useState<DateRange>('30d')
  const [customStart, setCustomStart] = useState(daysAgo(90))
  const [customEnd, setCustomEnd] = useState(today())

  const [analytics, setAnalytics] = useState<AnalyticsResponse | null>(null)
  const [loading, setLoading] = useState(false)

  // Derived date strings sent to API
  const startDate = useMemo(() => {
    if (dateRange === '7d') return daysAgo(7)
    if (dateRange === '30d') return daysAgo(30)
    return customStart
  }, [dateRange, customStart])

  const endDate = useMemo(() => {
    if (dateRange === '7d' || dateRange === '30d') return today()
    return customEnd
  }, [dateRange, customEnd])

  // Load operations list
  useEffect(() => {
    listOperations()
      .then(setOperations)
      .catch((e) => toast.error(`Failed to load operations: ${e.message}`))
  }, [])

  // Fetch analytics when operation or date changes
  useEffect(() => {
    if (!selectedOp) return
    setLoading(true)
    getAnalytics(selectedOp.operation_number, startDate, endDate)
      .then(setAnalytics)
      .catch((e) => toast.error(`Analytics error: ${e.message}`))
      .finally(() => setLoading(false))
  }, [selectedOp, startDate, endDate])

  const filtered = useMemo(() => {
    const searched = operations.filter(
      (op) =>
        op.operation_number.toLowerCase().includes(search.toLowerCase()) ||
        (op.process_name ?? '').toLowerCase().includes(search.toLowerCase()),
    )
    return [...searched].sort((a, b) => {
      if (sortMode === 'alpha')
        return a.operation_number.localeCompare(b.operation_number, undefined, { numeric: true })
      if (sortMode === 'records')
        return b.approved_count - a.approved_count
      if (sortMode === 'nok')
        return (b.nok_count ?? 0) - (a.nok_count ?? 0)
      // 'recent' — sort by last audit date descending
      return (b.last_audit_date ?? '').localeCompare(a.last_audit_date ?? '')
    })
  }, [operations, search, sortMode])

  return (
    <div className="flex min-h-screen flex-col overflow-hidden md:h-screen md:flex-row">
      {/* ------------------------------------------------------------------ */}
      {/* LEFT SIDEBAR — Operation selector                                    */}
      {/* ------------------------------------------------------------------ */}
      <aside className="flex max-h-[42vh] w-full shrink-0 flex-col border-b border-gray-200 bg-white md:max-h-none md:w-64 md:border-b-0 md:border-r">
        <div className="p-4 border-b border-gray-100 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-gray-800 text-sm">Operations</h3>
            <span className="text-xs text-gray-400">{filtered.length}</span>
          </div>
          {/* Search */}
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {/* Sort selector */}
          <div className="flex items-center gap-1.5">
            <ArrowUpDown size={12} className="text-gray-400 shrink-0" />
            <select
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
              className="flex-1 text-xs border border-gray-200 rounded-md px-2 py-1 text-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white"
            >
              <option value="alpha">A → Z</option>
              <option value="records">Most Records</option>
              <option value="nok">Highest NOK</option>
              <option value="recent">Most Recent</option>
            </select>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto" key={search}>
          {filtered.length === 0 && (
            <p className="text-xs text-gray-400 p-4 text-center">
              {operations.length === 0
                ? 'No approved operations yet. Review & approve rows first.'
                : 'No matches'}
            </p>
          )}
          {filtered.map((op) => (
            <button
              key={`${op.operation_number}__${op.approved_count}`}
              onClick={() => setSelectedOp(op)}
              className={`w-full text-left px-4 py-3 border-b border-gray-50 hover:bg-blue-50 transition-colors ${
                selectedOp?.operation_number === op.operation_number
                  ? 'bg-blue-50 border-l-2 border-l-blue-500'
                  : ''
              }`}
            >
              <div className="flex items-center justify-between gap-1">
                <p className="text-sm font-semibold text-gray-800 font-mono">
                  {op.operation_number}
                </p>
                {op.nok_count > 0 && (
                  <span className="text-xs font-semibold bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full shrink-0">
                    {op.nok_count} NG
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500 mt-0.5 truncate">{op.process_name ?? '—'}</p>
              <p className="text-xs text-blue-400 mt-0.5">{op.approved_count} records</p>
            </button>
          ))}
        </div>
      </aside>

      {/* ------------------------------------------------------------------ */}
      {/* MAIN CONTENT                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {!selectedOp ? (
          <div className="flex-1 flex items-center justify-center text-center p-8">
            <div>
              <BarChart2 size={48} className="text-gray-300 mx-auto mb-4" />
              <p className="text-gray-500 font-medium">Select an operation to view analytics</p>
              <p className="text-gray-400 text-sm mt-1">
                Only approved rows appear here
              </p>
            </div>
          </div>
        ) : (
          <>
            {/* Top bar */}
            <div className="shrink-0 border-b border-gray-200 bg-white px-4 py-4 sm:px-6">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                {/* Rich operation header */}
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-semibold text-blue-500 uppercase tracking-wider">
                      Operation
                    </span>
                    <span className="text-lg font-bold text-gray-900 font-mono">
                      {selectedOp.operation_number}
                    </span>
                  </div>
                  <p className="text-sm font-medium text-gray-700">
                    {selectedOp.process_name ?? '—'}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="flex items-center gap-1 text-xs text-gray-400">
                      <Database size={11} />
                      {analytics ? analytics.stats.total : selectedOp.approved_count} historical records
                    </span>
                    {analytics?.rows.length ? (
                      <span className="flex items-center gap-1 text-xs text-gray-400">
                        <Clock size={11} />
                        Last updated:{' '}
                        {(() => {
                          const sortedDates = analytics.rows
                            .map((r) => r.audit_date)
                            .filter(Boolean)
                            .sort()
                          const lastDate = sortedDates[sortedDates.length - 1]
                          try {
                            return lastDate ? format(parseISO(lastDate), 'dd MMM yyyy') : '—'
                          } catch { return '—' }
                        })()}
                      </span>
                    ) : null}
                  </div>
                </div>

                {/* Date range filters */}
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  {(['7d', '30d', 'custom'] as DateRange[]).map((r) => (
                    <button
                      key={r}
                      onClick={() => setDateRange(r)}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                        dateRange === r
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      {r === '7d' ? 'Last 7 days' : r === '30d' ? 'Last 30 days' : 'Custom'}
                    </button>
                  ))}

                  {dateRange === 'custom' && (
                    <div className="grid w-full grid-cols-[1fr_auto_1fr] items-center gap-2 sm:flex sm:w-auto">
                      <input
                        type="date"
                        className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        value={customStart}
                        onChange={(e) => setCustomStart(e.target.value)}
                      />
                      <span className="text-gray-400 text-sm">→</span>
                      <input
                        type="date"
                        className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        value={customEnd}
                        onChange={(e) => setCustomEnd(e.target.value)}
                      />
                    </div>
                  )}

                  {loading && <RefreshCw size={16} className="animate-spin text-blue-400" />}
                </div>
              </div>
            </div>

            {/* Analytics content */}
            <div className="flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
              {analytics && (
                <>
                  {/* OK/NOK summary cards */}
                  <OkNokSummary stats={analytics.stats} />

                  {/* Measurements table */}
                  <div className="card overflow-hidden">
                    <div className="border-b border-gray-100 px-4 py-4 sm:px-5">
                      <h3 className="font-semibold text-gray-800">Historical Measurements</h3>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {analytics.rows.length} record(s) · approved data only
                      </p>
                    </div>
                    <div className="overflow-x-auto">
                      <MeasurementsTable operationNumber={analytics.operation_number} rows={analytics.rows} />
                    </div>
                  </div>

                  {/* Trend chart */}
                  {analytics.rows.length > 0 && (
                    <div className="card p-5">
                      <h3 className="font-semibold text-gray-800 mb-4">Measurement Trend</h3>
                      <TrendChart rows={analytics.rows} />
                    </div>
                  )}

                  {analytics.rows.length === 0 && (
                    <div className="text-center py-12 text-gray-400">
                      <p>No approved data in selected date range.</p>
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
