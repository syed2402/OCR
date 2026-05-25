/**
 * Screen 2 — OCR Review
 *
 * Split layout: audit page image (left) | editable extracted fields (right).
 * Every row must be APPROVED before it enters the analytics engine.
 *
 * Keyboard shortcuts:
 *   →  / N   next row
 *   ←  / P   previous row
 *   A        approve current row
 *   R        reject current row
 */

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type WheelEvent as ReactWheelEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { AgGridReact } from 'ag-grid-react'
import { format, parseISO } from 'date-fns'
import {
  CellValueChangedEvent,
  ColDef,
  GridApi,
  RowClickedEvent,
  RowClassParams,
} from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Check,
  CheckCheck,
  X,
  Plus,
  Minus,
  RotateCcw,
  RotateCw,
  RefreshCw,
  BarChart2,
  Download,
  Columns3,
  Eye,
  Loader2,
  Pencil,
} from 'lucide-react'
import {
  approveRow,
  approveAll,
  getUploadRows,
  listUploads,
  rejectRow,
  reviewRow,
  rowImageUrl,
  isRowCrop,
  uploadFileUrl,
  ExtractedRow,
  UploadSummary,
} from '../api/client'

// ---------------------------------------------------------------------------
// Row status badge
// ---------------------------------------------------------------------------
function StatusBadge({ status }: { status: string }) {
  const cls: Record<string, string> = {
    EXTRACTED: 'status-extracted',
    REVIEWED: 'status-reviewed',
    APPROVED: 'status-approved',
    REJECTED: 'status-rejected',
  }
  return <span className={cls[status] ?? 'status-extracted'}>{status}</span>
}

// ---------------------------------------------------------------------------
// Confidence flag helper — highlight suspicious fields
// ---------------------------------------------------------------------------
function isSuspicious(row: ExtractedRow): Record<string, boolean> {
  const repeatedMeasurements =
    row.measurements.length >= 4 && new Set(row.measurements.map(String)).size <= 2
  const quantity = Number.isFinite(row.quantity) ? Number(row.quantity) : null
  return {
    operation_number: !row.operation_number,
    process_name: !row.process_name,
    audit_date: !row.audit_date,
    quantity: quantity === null || quantity < 0 || row.measurements.length > quantity,
    judgement: !row.judgement || !['OK', 'NOK'].includes(row.judgement.toUpperCase()),
    measurements:
      row.measurements.length === 0 ||
      row.measurements.some((m) => typeof m !== 'number' || isNaN(m)) ||
      (quantity !== null && row.measurements.length !== quantity) ||
      repeatedMeasurements,
  }
}

type SheetRow = ExtractedRow & {
  row_number: number
  measurement_count: number
  measurement_offset: number
  is_continuation: boolean
  [key: `m${number}`]: number | null
}

const DEFAULT_TORQUE_VALUE_COLUMNS = 6
const IMAGE_ZOOM_LEVELS = [50, 75, 100, 125, 150, 175, 200, 250, 300]

function allowedMeasurementCount(row?: Pick<ExtractedRow, 'quantity' | 'measurements'> | null) {
  if (!row || row.quantity === null || row.quantity === undefined || Number.isNaN(Number(row.quantity))) {
    return DEFAULT_TORQUE_VALUE_COLUMNS
  }
  return Math.max(0, Number(row.quantity))
}

function numericMeasurements(values: Array<string | number>, quantity?: number | null) {
  const limit = quantity === null || quantity === undefined ? values.length : Math.max(0, Number(quantity))
  return values
    .slice(0, limit)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value))
}

function valueMissing(row: Pick<ExtractedRow, 'quantity' | 'measurements'>, index: number) {
  if (row.quantity === null || row.quantity === undefined) return false
  if (index >= allowedMeasurementCount(row)) return false
  const value = row.measurements[index]
  return typeof value !== 'number' || !Number.isFinite(value)
}

function isRealOperationNumber(value?: string | null) {
  const text = String(value ?? '').trim()
  return Boolean(text && !['-', '—', '_'].includes(text) && /\d/.test(text))
}

function mergeWrappedRows(sourceRows: ExtractedRow[]) {
  const merged: ExtractedRow[] = []
  let current: ExtractedRow | null = null

  sourceRows.forEach((row) => {
    if (isRealOperationNumber(row.operation_number)) {
      const measurements = row.measurements.filter((value) => Number.isFinite(Number(value)))
      if (
        current &&
        (current.page ?? 1) === (row.page ?? 1) &&
        String(current.operation_number).trim() === String(row.operation_number).trim()
      ) {
        current = { ...row, measurements }
        merged.push(current)
        return
      }
      const quantity = row.quantity ?? (measurements.length || null)
      current = { ...row, quantity, measurements }
      merged.push(current)
      return
    }

    if (!current || (current.page ?? 1) !== (row.page ?? 1) || row.measurements.length === 0) {
      merged.push(row)
      return
    }

    const rowMeasurements = row.measurements.filter((value) => Number.isFinite(Number(value)))
    if (row.quantity !== null && row.quantity !== undefined) {
      current = {
        ...row,
        operation_number: current.operation_number,
        engine_number: row.engine_number ?? current.engine_number,
        process_name: row.process_name ?? current.process_name,
        measurements: rowMeasurements,
      }
      merged.push(current)
      return
    }

    const measurements = [...current.measurements, ...rowMeasurements]
    current.measurements = measurements
    if (current.quantity === null || current.quantity === undefined) {
      current.quantity = measurements.length
    }
  })

  return merged
}

function clampImageZoom(value: number) {
  return Math.max(40, Math.min(400, value))
}

function normalizeConfidence(value?: number | null) {
  if (value === null || value === undefined) return null
  const normalized = value > 1 ? value / 100 : value
  return Math.max(0, Math.min(1, normalized))
}

function confidenceToneClass(value?: number | null) {
  const normalized = normalizeConfidence(value)
  if (normalized === null) return ''
  if (normalized >= 0.95) return 'text-blue-800'
  if (normalized >= 0.8) return 'text-blue-600'
  return 'text-red-700'
}

function confidenceValueClass(value?: number | null) {
  return confidenceToneClass(value)
}

// ---------------------------------------------------------------------------
// Upload selector bar (shown when no uploadId in URL)
// ---------------------------------------------------------------------------
function UploadSelector({ onSelect }: { onSelect: (id: string) => void }) {
  const [uploads, setUploads] = useState<UploadSummary[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    listUploads()
      .then((u) => setUploads(u.filter((x) => x.status === 'COMPLETED')))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="p-8 text-gray-500 text-sm">Loading uploads…</div>

  if (uploads.length === 0)
    return (
      <div className="p-4 text-center sm:p-8">
        <p className="text-gray-500 mb-4">No completed uploads yet.</p>
        <button className="btn-primary" onClick={() => navigate('/')}>
          Upload a PDF
        </button>
      </div>
    )

  const formatUploadTime = (value: string | null) => {
    if (!value) return '-'
    try {
      return format(parseISO(value), 'dd MMM yyyy, hh:mm a')
    } catch {
      return value
    }
  }

  const sessionIdForUpload = (value: string | null) => {
    if (!value) return 'Analytics_TorqueAudit_unknown'
    try {
      return `Analytics_TorqueAudit_${format(parseISO(value), 'yyyyMMdd_HHmmss')}`
    } catch {
      return `Analytics_TorqueAudit_${value.replace(/[^0-9A-Za-z]/g, '')}`
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-8">
      <h2 className="mb-4 text-xl font-bold">Select Upload to Review</h2>
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-[760px] w-full border-collapse text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-20 border-r border-slate-200 px-4 py-3 text-left font-semibold">Sl No</th>
              <th className="border-r border-slate-200 px-4 py-3 text-left font-semibold">Session ID</th>
              <th className="border-r border-slate-200 px-4 py-3 text-left font-semibold">Opt Number</th>
              <th className="w-56 border-r border-slate-200 px-4 py-3 text-left font-semibold">Time</th>
              <th className="w-40 border-r border-slate-200 px-4 py-3 text-left font-semibold">Extracted Rows</th>
              <th className="w-28 border-r border-slate-200 px-4 py-3 text-left font-semibold">File</th>
              <th className="w-32 px-4 py-3 text-left font-semibold">Review</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {uploads.map((u, index) => (
              <tr key={u.upload_id} className="hover:bg-slate-50">
                <td className="border-r border-slate-200 px-4 py-3 font-mono text-slate-600">
                  {index + 1}
                </td>
                <td className="border-r border-slate-200 px-4 py-3 font-medium text-slate-900">
                  <span className="font-mono text-xs text-slate-700">
                    {sessionIdForUpload(u.created_at)}
                  </span>
                </td>
                <td className="border-r border-slate-200 px-4 py-3 font-medium text-slate-900">
                  {u.original_filename}
                </td>
                <td className="border-r border-slate-200 px-4 py-3 text-slate-600">
                  {formatUploadTime(u.created_at)}
                </td>
                <td className="border-r border-slate-200 px-4 py-3 font-mono text-slate-700">
                  {u.total_rows}
                </td>
                <td className="border-r border-slate-200 px-4 py-3">
                  <a
                    className="text-sm font-medium text-blue-600 hover:text-blue-800"
                    href={uploadFileUrl(u.upload_id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open
                  </a>
                </td>
                <td className="px-4 py-3">
                  <button
                    className="btn-primary py-1.5 px-3 text-xs"
                    onClick={() => onSelect(u.upload_id)}
                    type="button"
                  >
                    Review
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Review UI
// ---------------------------------------------------------------------------
export default function ReviewPage() {
  const { uploadId: urlUploadId } = useParams<{ uploadId: string }>()
  const navigate = useNavigate()
  const gridApiRef = useRef<GridApi<SheetRow> | null>(null)
  const reviewLayoutRef = useRef<HTMLDivElement | null>(null)
  const imagePaneRef = useRef<HTMLDivElement | null>(null)
  const imageDragRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    originX: number
    originY: number
  } | null>(null)

  const [uploadId, setUploadId] = useState<string | null>(urlUploadId ?? null)
  const [rows, setRows] = useState<ExtractedRow[]>([])
  const [idx, setIdx] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [approvingAll, setApprovingAll] = useState(false)
  const [isEditMode, setIsEditMode] = useState(false)
  const [activeSheet, setActiveSheet] = useState<number | 'ALL'>('ALL')
  const [rowHeight, setRowHeight] = useState(34)
  const [sheetPanelPercent, setSheetPanelPercent] = useState(62)
  const [sheetZoom, setSheetZoom] = useState(100)
  const [imageZoom, setImageZoom] = useState(100)
  const [imageRotation, setImageRotation] = useState(0)
  const [imagePan, setImagePan] = useState({ x: 0, y: 0 })
  const [isImagePanning, setIsImagePanning] = useState(false)
  const [isCompactLayout, setIsCompactLayout] = useState(false)
  const [showConfidence, setShowConfidence] = useState(false)
  const [measurementColumnCount, setMeasurementColumnCount] = useState(0)

  // Local editable state for the current row
  const [editOpNum, setEditOpNum] = useState('')
  const [editEngineNo, setEditEngineNo] = useState('')
  const [editProcName, setEditProcName] = useState('')
  const [editQuantity, setEditQuantity] = useState('')
  const [editDate, setEditDate] = useState('')
  const [editJudgement, setEditJudgement] = useState('')
  const [editMeasurements, setEditMeasurements] = useState<string[]>([])

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'))
    })
    return () => cancelAnimationFrame(frame)
  }, [sheetPanelPercent])

  useEffect(() => {
    const query = window.matchMedia('(max-width: 1023px)')
    const sync = () => setIsCompactLayout(query.matches)
    sync()
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    if (!isEditMode) gridApiRef.current?.stopEditing()
  }, [isEditMode])

  // Load rows when uploadId is known — suspicious rows bubble to top
  useEffect(() => {
    if (!uploadId) return
    let cancelled = false

    const loadRows = async (attempt = 1) => {
      try {
        const data = await getUploadRows(uploadId)
        if (cancelled) return
        // Keep backend page order — rows already sorted by page then id
        setRows(data)
        setIdx(0)
        setActiveSheet('ALL')
        setMeasurementColumnCount(DEFAULT_TORQUE_VALUE_COLUMNS)
        setLoading(false)
      } catch (e) {
        if (cancelled) return
        if (attempt < 3) {
          window.setTimeout(() => loadRows(attempt + 1), attempt * 1200)
          return
        }
        toast.error(`Failed to load rows: ${(e as Error).message}`)
        setLoading(false)
      }
    }

    setLoading(true)
    loadRows()
    return () => {
      cancelled = true
    }
  }, [uploadId])

  const row = rows[idx] ?? null

  // Populate edit fields whenever the current row changes
  useEffect(() => {
    if (!row) return
    setEditOpNum(row.operation_number ?? '')
    setEditEngineNo(row.engine_number ?? '')
    setEditProcName(row.process_name ?? '')
    setEditQuantity(row.quantity?.toString() ?? '')
    setEditDate(row.audit_date ?? '')
    setEditJudgement(row.judgement ?? '')
    setEditMeasurements(row.measurements.slice(0, allowedMeasurementCount(row)).map(String))
  }, [row?.id])

  useEffect(() => {
    imagePaneRef.current?.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
    setImageZoom(100)
    setImageRotation(0)
    setImagePan({ x: 0, y: 0 })
    imageDragRef.current = null
    setIsImagePanning(false)
  }, [row?.id])

  const resetImageView = useCallback(() => {
    setImageZoom(100)
    setImageRotation(0)
    setImagePan({ x: 0, y: 0 })
    imagePaneRef.current?.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
  }, [])

  const updateImageZoom = useCallback((nextZoom: number) => {
    setImageZoom(clampImageZoom(nextZoom))
  }, [])

  const handleImagePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!row?.row_image_path || event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    imageDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: imagePan.x,
      originY: imagePan.y,
    }
    setIsImagePanning(true)
  }, [imagePan.x, imagePan.y, row?.row_image_path])

  const handleImagePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = imageDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    setImagePan({
      x: drag.originX + event.clientX - drag.startX,
      y: drag.originY + event.clientY - drag.startY,
    })
  }, [])

  const stopImagePan = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (imageDragRef.current?.pointerId === event.pointerId) {
      imageDragRef.current = null
      setIsImagePanning(false)
    }
  }, [])

  const handleImageWheel = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    if (!row?.row_image_path) return
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    updateImageZoom(imageZoom + (event.deltaY < 0 ? 10 : -10))
  }, [imageZoom, row?.row_image_path, updateImageZoom])

  const flags = useMemo(() => (row ? isSuspicious(row) : {}), [row])

  const nav = useCallback(
    (delta: number) => {
      const next = idx + delta
      if (next >= 0 && next < rows.length) setIdx(next)
    },
    [idx, rows.length],
  )

  // Keyboard shortcuts — deps include handleApprove/handleReject to avoid stale closures
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowRight' || e.key === 'n') nav(1)
      if (e.key === 'ArrowLeft' || e.key === 'p') nav(-1)
      if (e.key === 'a' && row) handleApprove()
      if (e.key === 'r' && row) handleReject()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav, row, saving])

  // Build payload from editable fields
  const buildPayload = () => ({
    operation_number: editOpNum || undefined,
    engine_number: editEngineNo || undefined,
    process_name: editProcName || undefined,
    quantity: editQuantity === '' ? undefined : Number(editQuantity),
    audit_date: editDate || undefined,
    judgement: editJudgement || undefined,
    measurements: numericMeasurements(editMeasurements, editQuantity === '' ? undefined : Number(editQuantity)),
  })

  const buildPayloadFromRow = (source: ExtractedRow) => ({
    operation_number: source.operation_number || undefined,
    engine_number: source.engine_number || undefined,
    process_name: source.process_name || undefined,
    quantity: source.quantity ?? undefined,
    audit_date: source.audit_date || undefined,
    judgement: source.judgement || undefined,
    measurements: numericMeasurements(source.measurements, source.quantity),
  })

  const handleApprove = async () => {
    if (!row) return
    await handleApproveById(row.id, true)
      toast.success('Row approved ✓')
  }

  const handleApproveById = async (rowId: number, advance = false) => {
    const target = rows.find((r) => r.id === rowId)
    if (!target) return
    setSaving(true)
    try {
      if (target.id === row?.id) {
        await reviewRow(target.id, buildPayload())
      } else {
        await reviewRow(target.id, buildPayloadFromRow(target))
      }
      const approved = await approveRow(target.id)
      setRows((prev) => prev.map((r) => (r.id === approved.id ? approved : r)))
      toast.success('Row approved')
      if (advance && idx < rows.length - 1) nav(1)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleApproveAll = async () => {
    if (!uploadId) return
    toast(
      (t) => (
        <div className="flex flex-col gap-2">
          <p className="font-medium text-gray-800">Approve all {pendingCount} pending rows?</p>
          <p className="text-sm text-gray-500">They will go straight to analytics.</p>
          <div className="flex gap-2 mt-1">
            <button
              className="btn-success text-xs py-1 px-3"
              onClick={() => { toast.dismiss(t.id); _doApproveAll() }}
            >
              Approve All
            </button>
            <button
              className="btn-secondary text-xs py-1 px-3"
              onClick={() => toast.dismiss(t.id)}
            >
              Cancel
            </button>
          </div>
        </div>
      ),
      { duration: 10000 },
    )
  }

  const _doApproveAll = async () => {
    if (!uploadId) return
    setApprovingAll(true)
    try {
      const { approved } = await approveAll(uploadId)
      setRows((prev) => prev.map((r) =>
        r.review_status === 'EXTRACTED' || r.review_status === 'REVIEWED'
          ? { ...r, review_status: 'APPROVED' }
          : r
      ))
      toast.success(`${approved} rows approved — visible in Analytics now`)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setApprovingAll(false)
    }
  }

  const handleReject = async () => {
    if (!row) return
    setSaving(true)
    try {
      const rejected = await rejectRow(row.id)
      setRows((prev) => prev.map((r) => (r.id === rejected.id ? rejected : r)))
      toast('Row rejected', { icon: '🗑' })
      if (idx < rows.length - 1) nav(1)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleRejectById = async (rowId: number) => {
    setSaving(true)
    try {
      const rejected = await rejectRow(rowId)
      setRows((prev) => prev.map((r) => (r.id === rejected.id ? rejected : r)))
      toast('Row rejected')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const addMeasurement = () =>
    setEditMeasurements((m) => {
      const limit = editQuantity === '' ? DEFAULT_TORQUE_VALUE_COLUMNS : Math.max(0, Number(editQuantity))
      return m.length >= limit ? m : [...m, '']
    })
  const removeMeasurement = (i: number) =>
    setEditMeasurements((m) => m.filter((_, j) => j !== i))
  const updateMeasurement = (i: number, val: string) =>
    setEditMeasurements((m) => m.map((v, j) => (j === i ? val : v)))

  // Page-jump helpers
  const pages = useMemo(() => {
    const seen = new Set<number>()
    const list: number[] = []
    rows.forEach((r) => {
      const p = r.page ?? 1
      if (!seen.has(p)) { seen.add(p); list.push(p) }
    })
    return list.sort((a, b) => a - b)
  }, [rows])

  const currentPage = rows[idx]?.page ?? 1

  const jumpToPage = useCallback(
    (targetPage: number) => {
      const i = rows.findIndex((r) => (r.page ?? 1) === targetPage)
      if (i >= 0) setIdx(i)
    },
    [rows],
  )

  const jumpPageDelta = useCallback(
    (delta: number) => {
      const ci = pages.indexOf(currentPage)
      const next = pages[ci + delta]
      if (next !== undefined) jumpToPage(next)
    },
    [pages, currentPage, jumpToPage],
  )

  const sheetRows = useMemo<SheetRow[]>(() => {
    const visible = activeSheet === 'ALL'
      ? mergeWrappedRows(rows)
      : mergeWrappedRows(rows.filter((r) => (r.page ?? 1) === activeSheet))

    return visible.flatMap((r) => {
      const allowedCount = allowedMeasurementCount(r)
      const visibleMeasurements = r.measurements.slice(0, allowedCount)
      const chunkBasis = r.quantity === null || r.quantity === undefined
        ? Math.max(visibleMeasurements.length, 1)
        : Math.max(allowedCount, 1)
      const chunks = Math.max(1, Math.ceil(chunkBasis / DEFAULT_TORQUE_VALUE_COLUMNS))
      return Array.from({ length: chunks }, (_, chunkIndex) => {
        const measurementOffset = chunkIndex * DEFAULT_TORQUE_VALUE_COLUMNS
        const flat: SheetRow = {
          ...r,
          row_number: rows.findIndex((x) => x.id === r.id) + 1,
          measurement_count: visibleMeasurements.length,
          measurement_offset: measurementOffset,
          is_continuation: chunkIndex > 0,
        }
        for (let i = 0; i < measurementColumnCount; i++) {
          flat[`m${i}`] = measurementOffset + i < allowedCount
            ? visibleMeasurements[measurementOffset + i] ?? null
            : null
        }
        return flat
      })
    })
  }, [activeSheet, measurementColumnCount, rows])

  const selectRowById = useCallback((rowId: number) => {
    const nextIdx = rows.findIndex((r) => r.id === rowId)
    if (nextIdx >= 0) setIdx(nextIdx)
  }, [rows])

  const startPanelResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const layout = reviewLayoutRef.current
    if (!layout) return

    event.preventDefault()

    const updatePanelSplit = (clientX: number) => {
      const rect = layout.getBoundingClientRect()
      const nextPercent = ((clientX - rect.left) / rect.width) * 100
      setSheetPanelPercent(Math.min(82, Math.max(24, nextPercent)))
    }

    updatePanelSplit(event.clientX)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      updatePanelSplit(moveEvent.clientX)
    }

    const stopResize = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', stopResize)
      window.removeEventListener('pointercancel', stopResize)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', stopResize)
    window.addEventListener('pointercancel', stopResize)
  }, [])

  const saveGridEdit = useCallback(async (event: CellValueChangedEvent<SheetRow>) => {
    if (!isEditMode) return
    const edited = event.data
    if (!edited) return

    const field = String(event.colDef.field ?? '')
    const base = rows.find((r) => r.id === edited.id)
    if (!base) return

    const next: ExtractedRow = { ...base }
    if (field === 'operation_number') next.operation_number = edited.operation_number ?? null
    if (field === 'engine_number') next.engine_number = edited.engine_number ?? null
    if (field === 'process_name') next.process_name = edited.process_name ?? null
    if (field === 'quantity') {
      const quantity = event.newValue === '' || event.newValue === null ? null : Number(event.newValue)
      next.quantity = quantity !== null && Number.isFinite(quantity) ? Math.max(0, Math.floor(quantity)) : null
      next.measurements = numericMeasurements(base.measurements, next.quantity)
    }
    if (field === 'audit_date') next.audit_date = edited.audit_date ?? null
    if (field === 'judgement') next.judgement = edited.judgement ?? null
    if (/^m\d+$/.test(field)) {
      const measurementIndex = (edited.measurement_offset ?? 0) + Number(field.slice(1))
      const allowedCount = allowedMeasurementCount(base)
      if (measurementIndex >= allowedCount) return
      const measurements = [...base.measurements]
      const value = event.newValue === '' || event.newValue === null ? null : Number(event.newValue)
      if (value === null || Number.isNaN(value)) {
        measurements.splice(measurementIndex, 1)
      } else {
        measurements[measurementIndex] = value
      }
      next.measurements = numericMeasurements(measurements.filter((value) => value !== undefined), base.quantity)
    }

    setRows((prev) => prev.map((r) => (r.id === next.id ? { ...next, review_status: 'REVIEWED' } : r)))
    selectRowById(next.id)

    try {
      const reviewed = await reviewRow(next.id, buildPayloadFromRow(next))
      setRows((prev) => prev.map((r) => (r.id === reviewed.id ? reviewed : r)))
      toast.success('Cell saved')
    } catch (e: any) {
      setRows((prev) => prev.map((r) => (r.id === base.id ? base : r)))
      toast.error(e.message)
    }
  }, [isEditMode, rows, selectRowById])

  const columnDefs = useMemo<ColDef<SheetRow>[]>(() => {
    const editable = ({ data }: { data?: SheetRow }) => isEditMode && Boolean(data)
    const zoomWidth = (width: number) => Math.round(width * sheetZoom / 100)
    const displayedRows = (api: GridApi<SheetRow>) => {
      const visibleRows: SheetRow[] = []
      api.forEachNodeAfterFilterAndSort((node) => {
        if (node.data) visibleRows.push(node.data)
      })
      return visibleRows
    }
    const findDisplayedRowIndex = (params: { api: GridApi<SheetRow>; data?: SheetRow }) => {
      const visibleRows = displayedRows(params.api)
      const index = visibleRows.findIndex(
        (row) => row.id === params.data?.id && row.measurement_offset === params.data?.measurement_offset,
      )
      return { index, visibleRows }
    }
    const sameOperationGroup = (left?: SheetRow, right?: SheetRow) =>
      Boolean(
        left &&
        right &&
        (left.page ?? 1) === (right.page ?? 1) &&
        isRealOperationNumber(left.operation_number) &&
        String(left.operation_number).trim() === String(right.operation_number).trim() &&
        String(left.process_name ?? '').trim() === String(right.process_name ?? '').trim()
      )
    const rowSpanForRecordGroup = (params: { api: GridApi<SheetRow>; data?: SheetRow }) => {
      if (!params.data?.id) return 1

      const { index, visibleRows } = findDisplayedRowIndex(params)
      if (index === -1) return 1

      const currentId = params.data.id
      if (visibleRows[index - 1]?.id === currentId) return 1

      let span = 1
      for (let i = index + 1; i < visibleRows.length; i++) {
        if (visibleRows[i].id !== currentId) break
        span++
      }
      return span
    }
    const isRecordGroupContinuation = (params: { api: GridApi<SheetRow>; data?: SheetRow }) => {
      if (!params.data?.id) return false
      const { index, visibleRows } = findDisplayedRowIndex(params)
      return index > 0 && visibleRows[index - 1]?.id === params.data.id
    }
    const rowSpanForOperationGroup = (params: { api: GridApi<SheetRow>; data?: SheetRow }) => {
      if (!params.data || !isRealOperationNumber(params.data.operation_number)) return 1
      const { index, visibleRows } = findDisplayedRowIndex(params)
      if (index === -1 || sameOperationGroup(visibleRows[index - 1], params.data)) return 1
      let span = 1
      for (let i = index + 1; i < visibleRows.length; i++) {
        if (!sameOperationGroup(params.data, visibleRows[i])) break
        span++
      }
      return span
    }
    const isOperationGroupContinuation = (params: { api: GridApi<SheetRow>; data?: SheetRow }) => {
      const { index, visibleRows } = findDisplayedRowIndex(params)
      return index > 0 && sameOperationGroup(visibleRows[index - 1], params.data)
    }
    const mergedCellStyle = (
      params: { api: GridApi<SheetRow>; data?: SheetRow },
      spanGetter = rowSpanForOperationGroup,
      continuationGetter = isOperationGroupContinuation,
    ) => {
      const span = spanGetter(params)
      if (continuationGetter(params)) {
        return {
          alignItems: 'center',
          backgroundColor: '#fff',
          display: 'flex',
          fontWeight: 600,
          justifyContent: 'center',
          zIndex: 1,
        }
      }

      return {
        alignItems: 'center',
        backgroundColor: '#fff',
        display: 'flex',
        fontWeight: span > 1 ? 'bold' : 600,
        justifyContent: 'center',
        zIndex: span > 1 ? 2 : 1,
      }
    }
    const confidenceCellClass = (
      baseClass: string,
      valueGetter: (row?: SheetRow) => number | null | undefined,
    ) => ({ data }: { data?: SheetRow }) => {
      const value = normalizeConfidence(valueGetter(data))
      const classes = [baseClass]
      if (showConfidence && value !== null && value !== undefined) {
        if (value < 0.8) classes.push('review-confidence-bad')
        else classes.push('review-confidence-normal')
      }
      return classes.join(' ')
    }
    const withConfidence = (
      content: ReactNode,
      value?: number | null,
      align: 'left' | 'right' = 'left',
    ) => (
      <div className="relative flex h-full items-center">
        <div className={`${align === 'right' ? 'w-full text-right' : 'w-full'} ${showConfidence ? confidenceValueClass(value) : ''}`}>
          {content}
        </div>
      </div>
    )
    const measurementCols: ColDef<SheetRow>[] = Array.from({ length: measurementColumnCount }, (_, i) => ({
      headerName: `${i + 1}`,  // Just the number (1, 2, 3, 4, 5, 6)
      field: `m${i}` as keyof SheetRow & string,
      width: zoomWidth(86),
      editable: (params) => editable(params) && ((params.data?.measurement_offset ?? 0) + i) < allowedMeasurementCount(params.data),
      type: 'numericColumn',
      cellEditor: 'agNumberCellEditor',
      cellClass: ({ data }: { data?: SheetRow }) => {
        const classes = [
          confidenceCellClass('font-mono text-right', (row) => row?.confidence_scores?.measurements?.[i])({ data }),
        ]
        const slotIndex = data ? (data.measurement_offset ?? 0) + i : i
        if (data && slotIndex >= allowedMeasurementCount(data)) {
          classes.push('bg-slate-50 text-slate-300')
        } else if (data && data.quantity !== null && data.quantity !== undefined && valueMissing(data, slotIndex)) {
          classes.push('bg-red-50 text-red-700')
        }
        return classes.join(' ')
      },
      cellRenderer: ({ data, value }: { data?: SheetRow; value?: number | null }) =>
        withConfidence(value ?? '-', data?.confidence_scores?.measurements?.[i], 'right'),
      valueParser: ({ newValue }) => {
        if (newValue === '' || newValue === null || newValue === undefined) return null
        const value = Number(newValue)
        return Number.isNaN(value) ? null : value
      },
    }))

    return [
      {
        headerName: 'Opn Code',
        field: 'operation_number',
        width: zoomWidth(140),
        editable: (params) => editable(params) && !params.data?.is_continuation,
        cellClass: confidenceCellClass('font-mono font-semibold', (row) => row?.confidence_scores?.operation_number),
        cellRenderer: (params: { api: GridApi<SheetRow>; data?: SheetRow; value?: string | null }) =>
          isOperationGroupContinuation(params)
            ? null
            : withConfidence(params.value || '-', params.data?.confidence_scores?.operation_number),
        rowSpan: rowSpanForOperationGroup,
        cellStyle: (params) => mergedCellStyle(params),
      },
      {
        headerName: 'Engine No',
        field: 'engine_number',
        width: zoomWidth(150),
        editable: (params) => editable(params) && !params.data?.is_continuation,
        cellClass: confidenceCellClass('font-mono font-semibold', (row) => row?.confidence_scores?.engine_number),
        cellRenderer: (params: { api: GridApi<SheetRow>; data?: SheetRow; value?: string | null }) =>
          isOperationGroupContinuation(params)
            ? null
            : withConfidence(params.value || '-', params.data?.confidence_scores?.engine_number),
        rowSpan: rowSpanForOperationGroup,
        cellStyle: (params) => mergedCellStyle(params),
      },
      {
        headerName: 'Qty',
        field: 'quantity',
        width: zoomWidth(82),
        editable: (params) => editable(params) && !params.data?.is_continuation,
        type: 'numericColumn',
        cellEditor: 'agNumberCellEditor',
        cellClass: confidenceCellClass('font-mono font-semibold text-right', (row) => row?.confidence_scores?.quantity),
        cellRenderer: (params: { api: GridApi<SheetRow>; data?: SheetRow; value?: number | null }) =>
          isRecordGroupContinuation(params)
            ? null
            : withConfidence(params.value ?? '-', params.data?.confidence_scores?.quantity, 'right'),
        valueParser: ({ newValue }) => {
          if (newValue === '' || newValue === null || newValue === undefined) return null
          const value = Number(newValue)
          return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : null
        },
        rowSpan: rowSpanForRecordGroup,
        cellStyle: (params) => mergedCellStyle(params, rowSpanForRecordGroup, isRecordGroupContinuation),
      },
      ...measurementCols,
      {
        headerName: 'OK/NA/NOK',
        field: 'judgement',
        width: zoomWidth(120),
        editable: (params) => editable(params) && !params.data?.is_continuation,
        cellEditor: 'agSelectCellEditor',
        cellEditorParams: { values: ['', 'OK', 'NA', 'NOK'] },
        cellClass: confidenceCellClass('font-mono font-semibold text-center', (row) => row?.confidence_scores?.judgement),
        cellRenderer: ({ data, value }: { data?: SheetRow; value?: string | null }) =>
          data?.is_continuation
            ? null
            : withConfidence(value || '-', data?.confidence_scores?.judgement, 'right'),
      },
      {
        headerName: 'Actions',
        field: 'id',
        width: zoomWidth(88),
        editable: false,
        sortable: false,
        filter: false,
        cellRenderer: ({ data }: { data?: SheetRow }) => {
          if (!data) return null
          if (data.is_continuation) return <span className="text-xs text-slate-400">More values</span>
          if (data.review_status === 'APPROVED') return <span className="text-green-700 font-medium">Approved</span>
          if (data.review_status === 'REJECTED') return <span className="text-red-700 font-medium">Rejected</span>
          return (
            <div className="flex h-full items-center justify-center">
              <button
                className="flex h-8 w-8 items-center justify-center rounded border border-green-200 text-green-700 hover:bg-green-50"
                onClick={(e) => { e.stopPropagation(); handleApproveById(data.id) }}
                title="Confirm row"
                type="button"
              >
                <Check size={16} />
              </button>
            </div>
          )
        },
      },
    ]
  }, [handleApproveById, isEditMode, measurementColumnCount, sheetZoom, showConfidence])

  const getGridRowClass = useCallback((params: RowClassParams<SheetRow>) => {
    const data = params.data
    if (!data) return ''
    const classes = []
    const next = params.api.getDisplayedRowAtIndex((params.node.rowIndex ?? 0) + 1)?.data
    const previous = params.api.getDisplayedRowAtIndex((params.node.rowIndex ?? 0) - 1)?.data
    if (!previous || previous.id !== data.id) classes.push('review-grid-group-start')
    if (!next || next.id !== data.id) classes.push('review-grid-group-end')
    if (data.id === row?.id) classes.push('review-grid-selected')
    if (data.review_status === 'APPROVED') classes.push('review-grid-approved')
    if (data.review_status === 'REJECTED') classes.push('review-grid-rejected')
    if (isSuspicious(data).measurements || isSuspicious(data).judgement) classes.push('review-grid-warning')
    return classes.join(' ')
  }, [row?.id])

  // Progress stats
  const approvedCount = rows.filter((r) => r.review_status === 'APPROVED').length
  const rejectedCount = rows.filter((r) => r.review_status === 'REJECTED').length
  const pendingCount = rows.filter(
    (r) => r.review_status === 'EXTRACTED' || r.review_status === 'REVIEWED',
  ).length
  const imageTransform = `translate(${imagePan.x}px, ${imagePan.y}px) rotate(${imageRotation}deg) scale(${imageZoom / 100})`
  const canEditCurrentRow = Boolean(isEditMode && row)

  if (!uploadId) {
    return <UploadSelector onSelect={setUploadId} />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full p-20">
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="p-8 text-center">
        <p className="text-gray-500">No rows found for this upload.</p>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      {/* Top bar */}
      <div className="flex shrink-0 flex-col gap-3 border-b border-gray-200 bg-white px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-4">
          <h2 className="font-semibold text-gray-800">OCR Review</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          {/* Page navigation */}
          {pages.length > 1 && (
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg px-2 py-1">
              <button
                className="p-1 rounded hover:bg-gray-200 disabled:opacity-30"
                onClick={() => jumpPageDelta(-1)}
                disabled={pages.indexOf(currentPage) === 0}
                title="Previous page"
              >
                <ChevronsLeft size={14} />
              </button>
              <span className="text-sm font-medium text-gray-700 px-1">
                Page {pages.indexOf(currentPage) + 1} / {pages.length}
              </span>
              <button
                className="p-1 rounded hover:bg-gray-200 disabled:opacity-30"
                onClick={() => jumpPageDelta(1)}
                disabled={pages.indexOf(currentPage) === pages.length - 1}
                title="Next page"
              >
                <ChevronsRight size={14} />
              </button>
            </div>
          )}
          <span className="text-sm text-gray-500">
            Row {idx + 1} of {rows.length}
          </span>
          {pendingCount > 0 && (
            <button
              className="btn-success flex items-center gap-1 px-3 py-1"
              onClick={handleApproveAll}
              disabled={approvingAll}
              title="Approve all pending rows at once"
            >
              {approvingAll
                ? <Loader2 size={14} className="animate-spin" />
                : <CheckCheck size={14} />}
              Approve All ({pendingCount})
            </button>
          )}
          <button
            className="btn-secondary py-1 px-3"
            onClick={() => navigate('/analytics')}
          >
            <BarChart2 size={14} className="inline mr-1" />
            Analytics
          </button>
        </div>
      </div>

      {/* Page navigator */}
      <div className="shrink-0 overflow-x-auto border-b border-gray-200 bg-gray-50 px-4 py-2 sm:px-6">
        <div className="flex items-center gap-2">
          {pages.map((page) => {
            const hasCurrentRow = currentPage === page

            return (
              <div key={page} className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => {
                    setActiveSheet(page)
                    jumpToPage(page)
                  }}
                  className={`h-7 rounded px-3 text-xs font-semibold transition-colors ${
                    hasCurrentRow
                      ? 'bg-blue-600 text-white'
                      : 'bg-white text-slate-600 border border-slate-300 hover:bg-slate-100'
                  }`}
                  type="button"
                  title={`Show rows from page ${page}`}
                >
                  P{page}
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Spreadsheet review layout */}
      <div ref={reviewLayoutRef} className="flex flex-1 flex-col overflow-visible bg-white lg:flex-row lg:overflow-hidden">
        <div
          className="flex min-h-[50vh] min-w-0 shrink-0 flex-col bg-slate-50 lg:min-h-0"
          style={{ width: isCompactLayout ? '100%' : `calc(${sheetPanelPercent}% - 6px)` }}
        >
          <div className="flex flex-col gap-2 overflow-x-auto border-b border-slate-200 bg-white px-3 py-2 sm:flex-row sm:items-center">
            <button
              className={`rounded px-3 py-1.5 text-sm font-medium ${activeSheet === 'ALL' ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}
              onClick={() => setActiveSheet('ALL')}
              type="button"
            >
              All
            </button>
            <div className="flex shrink-0 flex-wrap items-center gap-2 sm:ml-auto sm:gap-3">
              <div className="flex rounded border border-slate-200 bg-slate-100 p-0.5">
                <button
                  className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
                    !isEditMode ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:text-slate-900'
                  }`}
                  onClick={() => setIsEditMode(false)}
                  type="button"
                  title="View only"
                >
                  <Eye size={14} /> View
                </button>
                <button
                  className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs font-semibold transition-colors ${
                    isEditMode ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:text-slate-900'
                  }`}
                  onClick={() => setIsEditMode(true)}
                  type="button"
                  title="Allow editing values"
                >
                  <Pencil size={14} /> Editable
                </button>
              </div>
              <label className="flex items-center gap-2 rounded border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600">
                <input
                  type="checkbox"
                  checked={showConfidence}
                  onChange={(e) => setShowConfidence(e.target.checked)}
                />
                AI confidence
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                Columns
                <input
                  className="w-24"
                  type="range"
                  min="75"
                  max="150"
                  value={sheetZoom}
                  onChange={(e) => setSheetZoom(Number(e.target.value))}
                  title="Zoom sheet columns"
                />
                <span className="w-9 text-right font-mono">{sheetZoom}%</span>
              </label>
              <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                Row
                <input
                  className="w-20"
                  type="range"
                  min="28"
                  max="64"
                  value={rowHeight}
                  onChange={(e) => setRowHeight(Number(e.target.value))}
                />
              </label>
              <button
                className="btn-secondary flex items-center gap-1 py-1.5 px-2 text-xs"
                onClick={() => gridApiRef.current?.autoSizeAllColumns()}
                type="button"
                title="Auto-size columns"
              >
                <Columns3 size={14} /> Auto
              </button>
              <button
                className="btn-secondary flex items-center gap-1 py-1.5 px-2 text-xs"
                onClick={() => gridApiRef.current?.exportDataAsCsv({ fileName: 'ocr-review.csv' })}
                type="button"
                title="Export CSV"
              >
                <Download size={14} /> CSV
              </button>
            </div>
          </div>

          <style>{`
            .review-sheet.ag-theme-quartz {
              --ag-font-family: Inter, ui-sans-serif, system-ui, sans-serif;
              --ag-header-background-color: #f8fafc;
              --ag-header-foreground-color: #334155;
              --ag-border-color: #cbd5e1;
              --ag-row-border-style: none;
              --ag-row-hover-color: #eef6ff;
              --ag-selected-row-background-color: #dbeafe;
            }
            .review-sheet .ag-row {
              border-bottom: 0 !important;
            }
            .review-sheet .ag-header-cell,
            .review-sheet .ag-cell {
              border-bottom: 1px solid #cbd5e1 !important;
              border-right: 1px solid #cbd5e1 !important;
            }
            .review-sheet .ag-header-cell:last-child,
            .review-sheet .ag-cell:last-child {
              border-right: 0 !important;
            }
            .review-sheet .review-grid-group-start .ag-cell {
              border-top: 1px solid #94a3b8 !important;
            }
            .review-sheet .review-grid-group-end .ag-cell {
              border-bottom: 1px solid #94a3b8 !important;
            }
            .review-sheet .review-grid-selected .ag-cell {
              box-shadow: inset 0 0 0 1px #2563eb;
            }
            .review-sheet .ag-cell.review-confidence-bad {
              color: #b91c1c;
            }
            .review-sheet .ag-cell.review-confidence-normal {
              color: #1d4ed8;
            }
            .review-sheet.review-sheet-view-only .ag-cell {
              cursor: default;
            }
          `}</style>
          <div className={`review-sheet ag-theme-quartz min-h-[360px] flex-1 lg:min-h-0 ${isEditMode ? '' : 'review-sheet-view-only'}`}>
            <AgGridReact<SheetRow>
              rowData={sheetRows}
              columnDefs={columnDefs}
              defaultColDef={{
                resizable: true,
                sortable: false,
                filter: true,
                editable: isEditMode,
                suppressKeyboardEvent: ({ event }) => event.key === 'Enter' && saving,
              }}
              onGridReady={(event) => { gridApiRef.current = event.api }}
              onRowClicked={(event: RowClickedEvent<SheetRow>) => {
                if (event.data) selectRowById(event.data.id)
              }}
              onCellValueChanged={saveGridEdit}
              getRowClass={getGridRowClass}
              rowSelection="multiple"
              rowHeight={rowHeight}
              headerHeight={38}
              singleClickEdit={isEditMode}
              suppressClickEdit={!isEditMode}
              animateRows
              suppressDragLeaveHidesColumns
              stopEditingWhenCellsLoseFocus
              suppressRowTransform
            />
          </div>
        </div>
        <div
          className="group hidden w-3 shrink-0 cursor-col-resize items-center justify-center border-x border-slate-300 bg-slate-200 hover:bg-blue-100 lg:flex"
          onPointerDown={startPanelResize}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize Excel sheet and image"
          title="Drag left/right to resize Excel sheet and image"
        >
          <div className="h-14 w-1 rounded-full bg-slate-400 group-hover:bg-blue-500" />
        </div>
        <div className="hidden">
          <table className="min-w-[980px] w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-slate-900 text-white">
              <tr className="[&>th]:border-r [&>th]:border-slate-700 [&>th]:px-2 [&>th]:py-2 [&>th]:text-left [&>th]:text-xs [&>th]:font-semibold [&>th]:uppercase">
                <th className="w-16">Card</th>
                <th className="w-14">Page</th>
                <th className="w-14">Row</th>
                <th className="w-28">Opn Code</th>
                <th>Process</th>
                <th className="w-32">Date</th>
                <th className="w-56">Measurements</th>
                <th className="w-24">Result</th>
                <th className="w-20">Done</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const selected = i === idx
                const rowFlags = isSuspicious(r)
                const locked = r.review_status === 'APPROVED'
                const rejected = r.review_status === 'REJECTED'
                const canEditRow = isEditMode && selected && !locked
                return (
                  <tr
                    key={r.id}
                    onClick={() => setIdx(i)}
                    className={`border-b border-slate-200 cursor-pointer align-top ${
                      selected ? 'bg-blue-50 ring-1 ring-inset ring-blue-400' : ''
                    } ${rejected ? 'bg-red-50 text-red-800' : ''}`}
                  >
                    <td className="border-r border-slate-200 px-2 py-2 font-semibold text-slate-700">
                      #{i + 1}
                      <div className="text-[10px] font-normal text-slate-400">Card</div>
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2 font-mono text-slate-600">
                      {r.page ?? 1}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2 font-mono text-slate-600">
                      {i + 1}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2">
                      {canEditRow ? (
                        <input
                          className={`w-full rounded border px-2 py-1 font-mono text-sm ${
                            flags.operation_number ? 'border-red-400 bg-red-50' : 'border-slate-300'
                          }`}
                          value={editOpNum}
                          onChange={(e) => setEditOpNum(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <span className={`font-mono font-semibold ${rowFlags.operation_number ? 'text-red-600' : ''}`}>
                          {r.operation_number || '-'}
                        </span>
                      )}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2">
                      {canEditRow ? (
                        <input
                          className={`w-full rounded border px-2 py-1 text-sm ${
                            flags.process_name ? 'border-red-400 bg-red-50' : 'border-slate-300'
                          }`}
                          value={editProcName}
                          onChange={(e) => setEditProcName(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <span className={rowFlags.process_name ? 'text-red-600' : ''}>
                          {r.process_name || '-'}
                        </span>
                      )}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2">
                      {canEditRow ? (
                        <input
                          type="date"
                          className={`w-full rounded border px-2 py-1 text-sm ${
                            flags.audit_date ? 'border-amber-400 bg-amber-50' : 'border-slate-300'
                          }`}
                          value={editDate}
                          onChange={(e) => setEditDate(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        r.audit_date || '-'
                      )}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2">
                      {canEditRow ? (
                        <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
                          {editMeasurements.map((val, mIdx) => (
                            <input
                              key={mIdx}
                              type="number"
                              step="0.01"
                              className={`w-16 rounded border px-1 py-1 text-center font-mono text-sm ${
                                val === '' || isNaN(Number(val)) ? 'border-red-400 bg-red-50' : 'border-slate-300'
                              }`}
                              value={val}
                              onChange={(e) => updateMeasurement(mIdx, e.target.value)}
                            />
                          ))}
                          <button
                            className="rounded border border-blue-200 px-2 text-xs text-blue-700 hover:bg-blue-50"
                            onClick={addMeasurement}
                            type="button"
                          >
                            +
                          </button>
                        </div>
                      ) : (
                        <span className={`font-mono ${rowFlags.measurements ? 'text-red-700 font-semibold' : ''}`}>
                          {r.measurements.length ? r.measurements.join(', ') : '-'}
                        </span>
                      )}
                    </td>
                    <td className="border-r border-slate-200 px-2 py-2">
                      {canEditRow ? (
                        <select
                          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                          value={editJudgement}
                          onChange={(e) => setEditJudgement(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <option value="">-</option>
                          <option value="OK">OK</option>
                          <option value="NOK">NOK</option>
                        </select>
                      ) : (
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${
                          r.judgement === 'NOK' ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'
                        }`}>
                          {r.judgement || '-'}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2">
                      {locked ? (
                        <span className="text-green-600">✓</span>
                      ) : rejected ? (
                        <span className="text-red-600">×</span>
                      ) : selected ? (
                        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                          <button className="text-green-600 hover:text-green-800" onClick={handleApprove} title="Approve">
                            ✓
                          </button>
                          <button className="text-red-500 hover:text-red-700" onClick={handleReject} title="Reject">
                            ×
                          </button>
                        </div>
                      ) : (
                        <span className="text-slate-300">-</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div
          className="flex min-h-[70vh] min-w-0 shrink-0 flex-col overflow-hidden bg-slate-100 lg:min-h-0"
          style={{ width: isCompactLayout ? '100%' : `calc(${100 - sheetPanelPercent}% - 6px)` }}
        >
          <div className="flex flex-col gap-2 bg-slate-900 px-3 py-2 text-white sm:flex-row sm:items-center sm:justify-between">
            <div>
              <span className="font-mono text-xs">#{idx + 1} · page {row?.page ?? '?'} · row {idx + 1}</span>
              {row?.row_image_path && (
                <p className="mt-0.5 text-[10px] font-medium uppercase tracking-wide text-blue-200">
                  {isRowCrop(row.row_image_path) ? 'Selected row crop' : 'Selected page image'}
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs font-medium text-slate-300">
                Drag image to pan
              </span>
              {row && <StatusBadge status={row.review_status} />}
            </div>
          </div>
          <div
            ref={imagePaneRef}
            className={`relative flex flex-1 touch-none select-none items-center justify-center overflow-hidden bg-slate-200 p-4 ${isImagePanning ? 'cursor-grabbing' : 'cursor-grab'}`}
            onPointerDown={handleImagePointerDown}
            onPointerMove={handleImagePointerMove}
            onPointerUp={stopImagePan}
            onPointerCancel={stopImagePan}
            onWheel={handleImageWheel}
          >
            {row?.row_image_path ? (
              <img
                key={`${row.id}-${row.row_image_path}`}
                src={rowImageUrl(row.row_image_path)}
                alt={`Row ${idx + 1} source image`}
                draggable={false}
                className="max-h-[92%] max-w-[92%] rounded-sm bg-white shadow-lg ring-2 ring-blue-500/60 transition-transform duration-100 ease-out"
                style={{
                  imageRendering: 'crisp-edges',
                  transform: imageTransform,
                  transformOrigin: 'center center',
                }}
              />
            ) : (
              <div className="mt-20 text-center text-sm text-slate-500">No image available</div>
            )}
            {row?.row_image_path && (
              <div
                className="absolute bottom-3 left-1/2 flex max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-2 overflow-x-auto rounded-md bg-slate-950/90 px-3 py-2 text-white shadow-xl backdrop-blur"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <select
                  className="h-9 rounded border border-slate-600 bg-slate-800 px-2 text-sm font-semibold text-white outline-none"
                  value={imageZoom}
                  onChange={(event) => updateImageZoom(Number(event.target.value))}
                  title="Zoom level"
                >
                  {!IMAGE_ZOOM_LEVELS.includes(imageZoom) && (
                    <option value={imageZoom}>{imageZoom}%</option>
                  )}
                  {IMAGE_ZOOM_LEVELS.map((level) => (
                    <option key={level} value={level}>
                      {level}%
                    </option>
                  ))}
                </select>
                <button
                  className="flex h-9 w-10 items-center justify-center rounded bg-slate-800 text-white hover:bg-slate-700"
                  onClick={() => updateImageZoom(imageZoom + 10)}
                  title="Zoom in"
                  type="button"
                >
                  <Plus size={18} />
                </button>
                <button
                  className="flex h-9 w-10 items-center justify-center rounded bg-slate-800 text-white hover:bg-slate-700"
                  onClick={() => updateImageZoom(imageZoom - 10)}
                  title="Zoom out"
                  type="button"
                >
                  <Minus size={18} />
                </button>
                <button
                  className="flex h-9 w-10 items-center justify-center rounded bg-slate-800 text-white hover:bg-slate-700"
                  onClick={() => setImageRotation((value) => value - 90)}
                  title="Rotate left"
                  type="button"
                >
                  <RotateCcw size={18} />
                </button>
                <button
                  className="flex h-9 w-10 items-center justify-center rounded bg-slate-800 text-white hover:bg-slate-700"
                  onClick={() => setImageRotation((value) => value + 90)}
                  title="Rotate right"
                  type="button"
                >
                  <RotateCw size={18} />
                </button>
                <button
                  className="flex h-9 items-center gap-2 rounded bg-slate-800 px-3 text-sm font-semibold text-white hover:bg-slate-700"
                  onClick={resetImageView}
                  title="Reset view"
                  type="button"
                >
                  <RefreshCw size={16} />
                  Reset view
                </button>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-slate-300 bg-white px-4 py-3">
            <div className="flex-1" />
            {row?.review_status !== 'APPROVED' && row?.review_status !== 'REJECTED' && (
              <>
                <button className="btn-danger flex items-center gap-2" onClick={handleReject} disabled={saving}>
                  <X size={16} /> Reject
                </button>
                <button className="btn-success flex items-center gap-2" onClick={handleApprove} disabled={saving}>
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                  Approve
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Main split */}
      <div className="hidden">
        {/* LEFT — page image */}
        <div className="w-1/2 bg-gray-800 flex flex-col overflow-hidden border-r border-gray-700">
          <div className="px-4 py-2 bg-gray-900 flex items-center justify-between">
            <span className="text-gray-300 text-xs font-mono">
              PDF page {row?.page ?? '?'} · Row {idx + 1}
            </span>
            {row && <StatusBadge status={row.review_status} />}
          </div>
          <div className="flex-1 overflow-auto p-3 flex items-start justify-center">
            {row?.row_image_path ? (
              <img
                src={rowImageUrl(row.row_image_path)}
                alt={`Row ${idx + 1} source image`}
                className="w-full rounded shadow-md bg-white"
                style={{ imageRendering: 'crisp-edges' }}
              />
            ) : (
              <div className="text-gray-500 text-sm mt-20 text-center">
                <p>No image available</p>
                <p className="text-xs mt-1">Image was not captured during extraction</p>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT — editable fields */}
        <div className="w-1/2 flex flex-col overflow-hidden bg-white">
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {row && (
              <div className="space-y-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                    Extracted Data
                  </span>
                  <StatusBadge status={row.review_status} />
                </div>

                {/* Operation Number */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Operation Number
                    {flags.operation_number && (
                      <span className="ml-2 text-red-500 text-xs">⚠ missing</span>
                    )}
                  </label>
                  <input
                    className={`input-field font-mono ${flags.operation_number ? 'input-error' : ''}`}
                    value={editOpNum}
                    onChange={(e) => setEditOpNum(e.target.value)}
                    placeholder="e.g. 1140"
                    disabled={!canEditCurrentRow}
                  />
                </div>

                {/* Process Name */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Process Name
                    {flags.process_name && (
                      <span className="ml-2 text-red-500 text-xs">⚠ missing</span>
                    )}
                  </label>
                  <input
                    className={`input-field ${flags.process_name ? 'input-error' : ''}`}
                    value={editProcName}
                    onChange={(e) => setEditProcName(e.target.value)}
                    placeholder="e.g. MB Cap tightening"
                    disabled={!canEditCurrentRow}
                  />
                </div>

                {/* Audit Date */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Audit Date
                    {flags.audit_date && (
                      <span className="ml-2 text-amber-600 text-xs">⚠ not detected</span>
                    )}
                  </label>
                  <input
                    type="date"
                    className={`input-field ${flags.audit_date ? 'input-error' : ''}`}
                    value={editDate}
                    onChange={(e) => setEditDate(e.target.value)}
                    disabled={!canEditCurrentRow}
                  />
                </div>

                {/* Judgement */}
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Judgement
                    {flags.judgement && (
                      <span className="ml-2 text-red-500 text-xs">⚠ unclear</span>
                    )}
                  </label>
                  <select
                    className={`input-field ${flags.judgement ? 'input-error' : ''}`}
                    value={editJudgement}
                    onChange={(e) => setEditJudgement(e.target.value)}
                    disabled={!canEditCurrentRow}
                  >
                    <option value="">— select —</option>
                    <option value="OK">OK</option>
                    <option value="NOK">NOK</option>
                  </select>
                </div>

                {/* Measurements */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-medium text-gray-600">
                      Measurements ({editMeasurements.length})
                      {flags.measurements && (
                        <span className="ml-2 text-red-500 text-xs">⚠ check values</span>
                      )}
                    </label>
                    {canEditCurrentRow && (
                      <button
                        className="text-blue-600 hover:text-blue-700 text-xs flex items-center gap-1"
                        onClick={addMeasurement}
                      >
                        <Plus size={12} /> Add
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-6 gap-2">
                    {editMeasurements.map((val, i) => (
                      <div key={i} className="relative">
                        <input
                          type="number"
                          className={`input-field font-mono text-center pr-6 ${
                            isNaN(Number(val)) || val === '' ? 'input-error' : ''
                          }`}
                          value={val}
                          onChange={(e) => updateMeasurement(i, e.target.value)}
                          placeholder="0"
                          disabled={!canEditCurrentRow}
                        />
                        {canEditCurrentRow && (
                          <button
                            className="absolute right-1 top-1/2 -translate-y-1/2 text-gray-400 hover:text-red-500"
                            onClick={() => removeMeasurement(i)}
                          >
                            <Minus size={12} />
                          </button>
                        )}
                      </div>
                    ))}
                    {editMeasurements.length === 0 && (
                      <p className="col-span-6 text-xs text-red-500">No measurements extracted</p>
                    )}
                  </div>
                </div>

                {/* ── Model Comparison Panel ── */}
                {false && row.agreement_score !== null && row.agreement_score !== undefined && (
                  <div className="mt-4 border border-gray-200 rounded-lg overflow-hidden">
                    <div className="px-4 py-2 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                      <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                        Model Comparison
                      </span>
                      {(row.agreement_score ?? 0) >= 80 ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-green-100 text-green-700">
                          ✓ Models agree
                        </span>
                      ) : (row.agreement_score ?? 0) >= 50 ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
                          ⚠ Partial disagreement
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                          ✗ Major disagreement
                        </span>
                      )}
                    </div>

                    <div className="px-4 py-3 space-y-2">
                      {/* Score bar */}
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400 w-20 shrink-0">Agreement</span>
                        <div className="flex-1 bg-gray-200 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full transition-all ${
                              (row.agreement_score ?? 0) >= 80 ? 'bg-green-500' :
                              (row.agreement_score ?? 0) >= 50 ? 'bg-amber-500' : 'bg-red-500'
                            }`}
                            style={{ width: `${row.agreement_score}%` }}
                          />
                        </div>
                        <span className="text-xs font-mono text-gray-600 w-8 text-right">
                          {row.agreement_score}
                        </span>
                      </div>

                      {/* Disagreements list */}
                      {row.disagreements?.length > 0 && (
                        <div className="space-y-1">
                          {row.disagreements.map((d, i) => (
                            <p key={i} className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1 font-mono">
                              {d}
                            </p>
                          ))}
                        </div>
                      )}

                      {/* Side-by-side raw results for major disagreement */}
                      {(row.agreement_score ?? 0) < 50 && (row.gemini_raw || row.gpt4o_raw) && (
                        <div className="grid grid-cols-2 gap-2 mt-2">
                          <div className="bg-blue-50 rounded p-2">
                            <p className="text-xs font-semibold text-blue-700 mb-1">Gemini</p>
                            <p className="text-xs text-blue-600 font-mono">
                              op: {(row.gemini_raw as any)?.operation_number ?? '—'}
                            </p>
                            <p className="text-xs text-blue-600 font-mono">
                              judge: {(row.gemini_raw as any)?.judgement ?? '—'}
                            </p>
                            <p className="text-xs text-blue-600 font-mono">
                              meas: [{((row.gemini_raw as any)?.measurements ?? []).join(', ')}]
                            </p>
                          </div>
                          <div className="bg-purple-50 rounded p-2">
                            <p className="text-xs font-semibold text-purple-700 mb-1">GPT-4o</p>
                            <p className="text-xs text-purple-600 font-mono">
                              op: {(row.gpt4o_raw as any)?.operation_number ?? '—'}
                            </p>
                            <p className="text-xs text-purple-600 font-mono">
                              judge: {(row.gpt4o_raw as any)?.judgement ?? '—'}
                            </p>
                            <p className="text-xs text-purple-600 font-mono">
                              meas: [{((row.gpt4o_raw as any)?.measurements ?? []).join(', ')}]
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Action bar */}
          <div className="border-t border-gray-200 px-6 py-4 flex items-center gap-3 shrink-0 bg-gray-50">
            <button
              className="btn-secondary py-2 px-3"
              onClick={() => nav(-1)}
              disabled={idx === 0 || saving}
            >
              <ChevronLeft size={16} />
            </button>
            <button
              className="btn-secondary py-2 px-3"
              onClick={() => nav(1)}
              disabled={idx === rows.length - 1 || saving}
            >
              <ChevronRight size={16} />
            </button>

            <div className="flex-1" />

            {row?.review_status !== 'APPROVED' && row?.review_status !== 'REJECTED' && (
              <>
                <button
                  className="btn-danger flex items-center gap-2"
                  onClick={handleReject}
                  disabled={saving}
                  title="Reject (R)"
                >
                  <X size={16} /> Reject
                </button>
                <button
                  className="btn-success flex items-center gap-2"
                  onClick={handleApprove}
                  disabled={saving}
                  title="Approve (A)"
                >
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                  Approve
                </button>
              </>
            )}
            {row?.review_status === 'APPROVED' && (
              <span className="text-green-700 text-sm font-medium flex items-center gap-1">
                <Check size={14} /> Approved
              </span>
            )}
            {row?.review_status === 'REJECTED' && (
              <span className="text-red-700 text-sm font-medium flex items-center gap-1">
                <X size={14} /> Rejected
              </span>
            )}
          </div>

          {/* Keyboard hint */}
          <div className="px-6 py-2 bg-gray-50 border-t border-gray-100 text-xs text-gray-400 flex gap-4">
            <span>← → Navigate</span>
            <span>A Approve</span>
            <span>R Reject</span>
          </div>
        </div>
      </div>
    </div>
  )
}
