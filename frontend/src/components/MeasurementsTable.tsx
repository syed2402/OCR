import { useMemo } from 'react'
import { format, parseISO } from 'date-fns'
import { AnalyticsRow } from '../api/client'

interface Props {
  operationNumber: string
  rows: AnalyticsRow[]
}

function formatDate(value: string) {
  try {
    return format(parseISO(value), 'dd/MM/yy')
  } catch {
    return value
  }
}

function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '')
}

function isOk(row: AnalyticsRow) {
  return (row.judgement ?? '').toUpperCase() === 'OK'
}

function isNok(row: AnalyticsRow) {
  return ['NOK', 'NG'].includes((row.judgement ?? '').toUpperCase())
}

function average(values: number[]) {
  if (!values.length) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function nokByLimit(value: number | null | undefined, lower?: number | null, upper?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return false
  if (lower === null || lower === undefined || upper === null || upper === undefined) return false
  return value < lower || value > upper
}

function statusByLimit(value: number | null | undefined, lower?: number | null, upper?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  if (lower === null || lower === undefined || upper === null || upper === undefined) return ''
  return value >= lower && value <= upper ? 'OK' : 'NOK'
}

export default function MeasurementsTable({ operationNumber, rows }: Props) {
  const matrix = useMemo(() => {
    const dates = Array.from(
      new Set(rows.map((row) => row.audit_date).filter((date): date is string => Boolean(date))),
    ).sort()

    const maxMeasurements = Math.max(0, ...rows.map((row) => row.measurements.length))
    const firstWithLimits = rows.find(
      (row) => row.lower_limit !== null || row.upper_limit !== null,
    )

    return {
      dates,
      maxMeasurements,
      lowerLimit: firstWithLimits?.lower_limit ?? null,
      upperLimit: firstWithLimits?.upper_limit ?? null,
    }
  }, [rows])

  if (!rows.length) {
    return <p className="text-center text-gray-400 py-8 text-sm">No data</p>
  }

  const rowsForDate = (date: string) => rows.filter((row) => row.audit_date === date)

  const nokCountForDate = (date: string) => {
    const dateRows = rowsForDate(date)
    const hasLimits = matrix.lowerLimit !== null && matrix.upperLimit !== null
    if (!hasLimits) return dateRows.filter(isNok).length

    return dateRows.reduce(
      (sum, row) =>
        sum + row.measurements.filter((value) => nokByLimit(value, row.lower_limit ?? matrix.lowerLimit, row.upper_limit ?? matrix.upperLimit)).length,
      0,
    )
  }

  const okCountForDate = (date: string) => {
    const dateRows = rowsForDate(date)
    const hasLimits = matrix.lowerLimit !== null && matrix.upperLimit !== null
    if (!hasLimits) return dateRows.filter(isOk).length

    const totalValues = dateRows.reduce((sum, row) => sum + row.measurements.length, 0)
    return Math.max(0, totalValues - nokCountForDate(date))
  }

  const measurementCountForDate = (date: string) =>
    rowsForDate(date).reduce((sum, row) => sum + row.measurements.length, 0)

  const limitLabel =
    matrix.lowerLimit !== null && matrix.upperLimit !== null
      ? `${formatNumber(matrix.lowerLimit)}-${formatNumber(matrix.upperLimit)}`
      : ''

  return (
    <div className="overflow-x-auto">
      <table className="min-w-[760px] w-full border-collapse text-sm">
        <tbody>
          <tr>
            <th className="sticky left-0 z-10 w-56 border border-slate-300 bg-white px-4 py-3 text-left font-semibold text-slate-800">
              Opn Code
            </th>
            <td className="border border-slate-300 px-4 py-3 text-center font-mono font-semibold" colSpan={matrix.dates.length}>
              {operationNumber}
              {limitLabel && <span className="ml-8 text-slate-500">{limitLabel}</span>}
            </td>
          </tr>

          <tr className="bg-slate-50">
            <th className="sticky left-0 z-10 border border-slate-300 bg-slate-50 px-4 py-3 text-left font-semibold text-slate-800">
              Date
            </th>
            {matrix.dates.map((date) => (
              <td key={date} className="min-w-36 border border-slate-300 px-4 py-3 text-center font-mono font-semibold">
                {formatDate(date)}
              </td>
            ))}
          </tr>

          {[
            [
              'No of measurements',
              measurementCountForDate,
            ],
            ['OK count', okCountForDate],
            ['NOK count', nokCountForDate],
            [
              'NOK %',
              (date: string) => {
                const total = measurementCountForDate(date)
                return total ? `${((nokCountForDate(date) / total) * 100).toFixed(1)}%` : ''
              },
            ],
          ].map(([label, getter]) => (
            <tr key={String(label)}>
              <th className="sticky left-0 z-10 border border-slate-300 bg-white px-4 py-3 text-left font-medium text-slate-700">
                {String(label)}
              </th>
              {matrix.dates.map((date) => (
                <td key={date} className="border border-slate-300 px-4 py-3 text-center font-mono">
                  {(getter as (date: string) => string | number)(date)}
                </td>
              ))}
            </tr>
          ))}

          <tr className="bg-slate-100">
            <th className="sticky left-0 z-10 border border-slate-300 bg-slate-100 px-4 py-3 text-left font-semibold text-slate-800">
              Parameter
            </th>
            <th className="w-20 border border-slate-300 bg-slate-100 px-2 py-3 text-center text-xs font-semibold text-slate-800">
              Upper Limit
            </th>
            <th className="w-20 border border-slate-300 bg-slate-100 px-2 py-3 text-center text-xs font-semibold text-slate-800">
              Lower Limit
            </th>
            {matrix.dates.map((date) => (
              <th key={date} className="border border-slate-300 bg-slate-100 px-4 py-3" />
            ))}
          </tr>

          {Array.from({ length: matrix.maxMeasurements }, (_, index) => (
            <tr key={index}>
              <th className="sticky left-0 z-10 border border-slate-300 bg-white px-4 py-3 text-left font-semibold text-slate-800">
                M{index + 1}
              </th>
              <td className="w-20 border border-slate-300 px-2 py-3 text-center font-mono">
                {formatNumber(matrix.upperLimit)}
              </td>
              <td className="w-20 border border-slate-300 px-2 py-3 text-center font-mono">
                {formatNumber(matrix.lowerLimit)}
              </td>
              {matrix.dates.map((date) => {
                const values = rowsForDate(date)
                  .map((row) => row.measurements[index])
                  .filter((value): value is number => typeof value === 'number')
                const value = average(values)
                const status = statusByLimit(value, matrix.lowerLimit, matrix.upperLimit)
                return (
                  <td key={date} className="border border-slate-300 px-4 py-3 text-center font-mono">
                    {value === null ? '' : (
                      <span className="inline-flex items-center justify-center gap-2">
                        <span>{formatNumber(value)}</span>
                        {status && (
                          <span className={status === 'OK' ? 'font-semibold text-green-700' : 'font-semibold text-red-700'}>
                            {status}
                          </span>
                        )}
                      </span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
