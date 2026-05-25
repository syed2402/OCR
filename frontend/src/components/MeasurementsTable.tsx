import { useMemo } from 'react'
import { format, parseISO } from 'date-fns'
import { AnalyticsRow } from '../api/client'

interface Props {
  operationNumber: string
  rows: AnalyticsRow[]
}

type MeasurementCell = {
  value: number
  lower: number | null | undefined
  upper: number | null | undefined
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
    const columns = Array.from(
      new Map(
        rows.map((row) => {
          const key = row.column_key ?? row.audit_date ?? String(row.id)
          return [key, { key, label: row.column_label ?? row.audit_date ?? row.upload_filename ?? key }]
        }),
      ).values(),
    ).sort((left, right) => left.label.localeCompare(right.label))

    const maxMeasurements = Math.max(0, ...rows.map((row) => row.measurements.length))
    const firstWithLimits = rows.find(
      (row) => row.lower_limit !== null || row.upper_limit !== null,
    )

    return {
      columns,
      maxMeasurements: Math.max(
        maxMeasurements,
        ...columns.map((column) =>
          rows
            .filter((row) => (row.column_key ?? row.audit_date ?? String(row.id)) === column.key)
            .reduce((sum, row) => sum + row.measurements.length, 0),
        ),
      ),
      lowerLimit: firstWithLimits?.lower_limit ?? null,
      upperLimit: firstWithLimits?.upper_limit ?? null,
    }
  }, [rows])

  if (!rows.length) {
    return <p className="text-center text-gray-400 py-8 text-sm">No data</p>
  }

  const rowsForColumn = (columnKey: string) =>
    rows.filter((row) => (row.column_key ?? row.audit_date ?? String(row.id)) === columnKey)

  const measurementsForColumn = (columnKey: string): MeasurementCell[] =>
    rowsForColumn(columnKey).flatMap((row) =>
      row.measurements
        .filter((value): value is number => typeof value === 'number')
        .map((value) => ({
          value,
          lower: row.lower_limit ?? matrix.lowerLimit,
          upper: row.upper_limit ?? matrix.upperLimit,
        })),
    )

  const nokCountForColumn = (columnKey: string) => {
    const dateRows = rowsForColumn(columnKey)
    const hasLimits = matrix.lowerLimit !== null && matrix.upperLimit !== null
    if (!hasLimits) return dateRows.filter(isNok).length

    return dateRows.reduce(
      (sum, row) =>
        sum + row.measurements.filter((value) => nokByLimit(value, row.lower_limit ?? matrix.lowerLimit, row.upper_limit ?? matrix.upperLimit)).length,
      0,
    )
  }

  const okCountForColumn = (columnKey: string) => {
    const dateRows = rowsForColumn(columnKey)
    const hasLimits = matrix.lowerLimit !== null && matrix.upperLimit !== null
    if (!hasLimits) return dateRows.filter(isOk).length

    const totalValues = dateRows.reduce((sum, row) => sum + row.measurements.length, 0)
    return Math.max(0, totalValues - nokCountForColumn(columnKey))
  }

  const measurementCountForColumn = (columnKey: string) =>
    rowsForColumn(columnKey).reduce((sum, row) => sum + row.measurements.length, 0)

  const limitForColumn = (columnKey: string, field: 'upper_limit' | 'lower_limit') => {
    const dateRows = rowsForColumn(columnKey)
    const rowWithLimit = dateRows.find((row) => row[field] !== null && row[field] !== undefined)
    return formatNumber(rowWithLimit?.[field] ?? matrix[field === 'upper_limit' ? 'upperLimit' : 'lowerLimit'])
  }

  return (
    <div className="overflow-x-auto">
      <table
        className="table-fixed border-collapse text-sm"
        style={{ width: `${176 + matrix.columns.length * 128}px` }}
      >
        <colgroup>
          <col style={{ width: 176 }} />
          {matrix.columns.map((column) => (
            <col key={column.key} style={{ width: 128 }} />
          ))}
        </colgroup>
        <tbody>
          <tr>
            <th className="sticky left-0 z-10 border border-slate-300 bg-white px-4 py-3 text-left font-semibold text-slate-800">
              Opn Code
            </th>
            <td className="border border-slate-300 px-4 py-3 text-center font-mono font-semibold" colSpan={matrix.columns.length}>
              {operationNumber}
            </td>
          </tr>

          <tr className="bg-slate-50">
            <th className="sticky left-0 z-10 border border-slate-300 bg-slate-50 px-4 py-3 text-left font-semibold text-slate-800">
              Date
            </th>
            {matrix.columns.map((column) => (
              <td key={column.key} className="border border-slate-300 px-3 py-3 text-center font-mono font-semibold">
                {formatDate(column.label)}
              </td>
            ))}
          </tr>

          {[
            ['Upper Limit', (columnKey: string) => limitForColumn(columnKey, 'upper_limit')],
            ['Lower Limit', (columnKey: string) => limitForColumn(columnKey, 'lower_limit')],
            [
              'No of measurements',
              measurementCountForColumn,
            ],
            ['OK count', okCountForColumn],
            ['NOK count', nokCountForColumn],
            [
              'NOK %',
              (columnKey: string) => {
                const total = measurementCountForColumn(columnKey)
                return total ? `${((nokCountForColumn(columnKey) / total) * 100).toFixed(1)}%` : ''
              },
            ],
          ].map(([label, getter]) => (
            <tr key={String(label)}>
              <th className="sticky left-0 z-10 border border-slate-300 bg-white px-4 py-3 text-left font-medium text-slate-700">
                {String(label)}
              </th>
              {matrix.columns.map((column) => (
                <td key={column.key} className="border border-slate-300 px-3 py-3 text-center font-mono">
                  {(getter as (columnKey: string) => string | number)(column.key)}
                </td>
              ))}
            </tr>
          ))}

          <tr className="bg-slate-100">
            <th className="sticky left-0 z-10 border border-slate-300 bg-slate-100 px-4 py-3 text-left font-semibold text-slate-800">
              Parameter
            </th>
            {matrix.columns.map((column) => (
              <th key={column.key} className="border border-slate-300 bg-slate-100 px-4 py-3" />
            ))}
          </tr>

          {Array.from({ length: matrix.maxMeasurements }, (_, index) => (
            <tr key={index}>
              <th className="sticky left-0 z-10 border border-slate-300 bg-white px-4 py-3 text-left font-semibold text-slate-800">
                M{index + 1}
              </th>
              {matrix.columns.map((column) => {
                const measurement = measurementsForColumn(column.key)[index]
                const status = statusByLimit(measurement?.value, measurement?.lower, measurement?.upper)
                return (
                  <td key={column.key} className="border border-slate-300 px-3 py-3 text-center font-mono">
                    {!measurement ? '' : (
                      <span className={status === 'NOK' ? 'font-semibold text-red-700' : 'text-slate-950'}>
                        {formatNumber(measurement.value)}
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
