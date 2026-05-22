import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDropzone } from 'react-dropzone'
import toast from 'react-hot-toast'
import { FileText, Upload, CheckCircle, XCircle, Clock, ArrowRight, AlertCircle, RefreshCw, Trash2 } from 'lucide-react'
import {
  uploadPdf, getUploadStatus, listUploads, getUploadPages, retryPage, deleteUpload,
  UploadStatus, UploadSummary, PageSummary,
} from '../api/client'
import { format, parseISO } from 'date-fns'

type ActiveUpload = {
  uploadId: string
  filename: string
  status: UploadStatus | null
}

export default function UploadPage() {
  const navigate = useNavigate()
  const [active, setActive] = useState<ActiveUpload | null>(null)
  const [uploading, setUploading] = useState(false)
  const [history, setHistory] = useState<UploadSummary[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollFailuresRef = useRef(0)

  useEffect(() => {
    listUploads().then(setHistory).catch(() => {})
  }, [])

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPolling = (uploadId: string) => {
    stopPolling()
    pollFailuresRef.current = 0
    pollRef.current = setInterval(async () => {
      try {
        const status = await getUploadStatus(uploadId)
        pollFailuresRef.current = 0
        setActive((prev) => (prev ? { ...prev, status } : prev))
        if (status.status === 'COMPLETED' || status.status === 'FAILED') {
          stopPolling()
          if (status.status === 'COMPLETED') {
            toast.success(`Extracted ${status.total_rows} rows from ${status.original_filename}`)
          } else {
            toast.error(`Processing failed: ${status.error_message}`)
          }
          listUploads().then(setHistory).catch(() => {})
        }
      } catch (err: any) {
        pollFailuresRef.current += 1
        if (pollFailuresRef.current < 3) return

        stopPolling()
        const message =
          err?.message === 'Upload not found'
            ? 'Upload record was not found. Please retry the upload.'
            : `Unable to read processing status: ${err?.message || 'server error'}`

        setActive((prev) =>
          prev
            ? {
                ...prev,
                status: {
                  upload_id: uploadId,
                  status: 'FAILED',
                  total_rows: 0,
                  processed_pages: 0,
                  original_filename: prev.filename,
                  error_message: message,
                  created_at: null,
                  completed_at: new Date().toISOString(),
                },
              }
            : prev,
        )
        toast.error(message)
        listUploads().then(setHistory).catch(() => {})
        // transient error — keep polling
      }
    }, 2000)
  }

  useEffect(() => () => stopPolling(), [])

  const onDrop = useCallback(
    async (accepted: File[]) => {
      const file = accepted[0]
      if (!file) return
      setUploading(true)
      try {
        const { upload_id, filename } = await uploadPdf(file)
        setActive({ uploadId: upload_id, filename, status: null })
        startPolling(upload_id)
        toast.success('Upload started — extracting data…')
      } catch (err: any) {
        toast.error(`Upload failed: ${err.message}`)
      } finally {
        setUploading(false)
      }
    },
    [],
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'application/pdf': ['.pdf'] },
    multiple: false,
    disabled: uploading || active?.status?.status === 'PROCESSING',
  })

  const processing = active?.status?.status === 'PROCESSING' || (active && !active.status)
  const completed = active?.status?.status === 'COMPLETED'
  const failed = active?.status?.status === 'FAILED'

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-gray-900">Upload Audit Sheet</h2>
        <p className="text-gray-500 mt-1 text-sm">
          Upload a Stellantis audit PDF. Gemini Vision will extract all operation rows for your review.
        </p>
      </div>

      {/* Drop zone */}
      <div
        {...getRootProps()}
        className={`card p-10 text-center cursor-pointer border-2 border-dashed transition-all ${
          isDragActive
            ? 'border-blue-500 bg-blue-50'
            : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
        } ${processing ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-3">
          <div className="w-14 h-14 bg-blue-100 rounded-full flex items-center justify-center">
            <Upload className="text-blue-600" size={24} />
          </div>
          {isDragActive ? (
            <p className="text-blue-600 font-medium">Drop the PDF here…</p>
          ) : (
            <>
              <p className="font-medium text-gray-700">Drag & drop a PDF, or click to browse</p>
              <p className="text-sm text-gray-400">Torque Audit Sheet · Process Audit Check Sheet</p>
            </>
          )}
        </div>
      </div>

      {/* Active upload progress */}
      {active && (
        <div className="card mt-6 p-5">
          <div className="flex items-start gap-4">
            <div className="shrink-0 mt-0.5">
              {processing && (
                <div className="w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full animate-spin" />
              )}
              {completed && <CheckCircle className="text-green-500" size={32} />}
              {failed && <XCircle className="text-red-500" size={32} />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="font-semibold text-gray-800 truncate">{active.filename}</p>
                  <p className="text-sm text-gray-500 mt-0.5">
                    {processing && 'Extracting data from pages…'}
                    {completed &&
                      `${active.status?.total_rows} rows extracted from ${active.status?.processed_pages} page(s)`}
                    {failed && (active.status?.error_message || 'Processing failed')}
                  </p>
                </div>
                {completed && (
                  <button
                    className="btn-primary flex items-center gap-2 whitespace-nowrap"
                    onClick={() => navigate(`/review/${active.uploadId}`)}
                  >
                    Review Rows <ArrowRight size={16} />
                  </button>
                )}
              </div>

              {/* Progress bar */}
              {processing && active.status && (
                <div className="mt-3">
                  <div className="flex justify-between text-xs text-gray-400 mb-1">
                    <span>Pages processed: {active.status.processed_pages}</span>
                    <span>Rows found: {active.status.total_rows}</span>
                  </div>
                  <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500 rounded-full animate-pulse w-3/4" />
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Tip */}
      <div className="mt-4 flex items-start gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3">
        <AlertCircle size={16} className="shrink-0 mt-0.5" />
        <span>
          After extraction, all rows must be reviewed and approved before they appear in analytics.
        </span>
      </div>

      {/* Recent uploads */}
      {history.length > 0 && (
        <div className="mt-10">
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Recent Uploads
          </h3>
          <div className="card divide-y divide-gray-100">
            {history.map((u) => (
              <UploadRow
                key={u.upload_id}
                upload={u}
                onNavigate={() => navigate(`/review/${u.upload_id}`)}
                onDeleted={() => setHistory((h) => h.filter((x) => x.upload_id !== u.upload_id))}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Per-upload row with expandable page breakdown + retry
// ---------------------------------------------------------------------------
function UploadRow({ upload, onNavigate, onDeleted }: { upload: UploadSummary; onNavigate: () => void; onDeleted: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [pages, setPages] = useState<PageSummary[] | null>(null)
  const [retrying, setRetrying] = useState<number | null>(null)
  const [deleting, setDeleting] = useState(false)

  const loadPages = async () => {
    if (pages) return
    try {
      const p = await getUploadPages(upload.upload_id)
      setPages(p)
    } catch {
      toast.error('Could not load page info')
    }
  }

  const handleExpand = () => {
    if (!expanded) loadPages()
    setExpanded((e) => !e)
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteUpload(upload.upload_id)
      toast.success('Upload deleted')
      onDeleted()
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message}`)
      setDeleting(false)
    }
  }

  const handleRetry = async (page: number) => {
    setRetrying(page)
    try {
      await retryPage(upload.upload_id, page)
      toast.success(`Retrying page ${page} — check back in ~30 seconds`)
      // Refresh page list after a delay
      setTimeout(async () => {
        const p = await getUploadPages(upload.upload_id)
        setPages(p)
        setRetrying(null)
      }, 35000)
    } catch (e: any) {
      toast.error(`Retry failed: ${e.message}`)
      setRetrying(null)
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3 px-5 py-3">
        <FileText size={16} className="text-gray-400 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-800 truncate">{upload.original_filename}</p>
          <p className="text-xs text-gray-400">
            {upload.created_at ? format(parseISO(upload.created_at), 'dd MMM yyyy, HH:mm') : '—'} ·{' '}
            {upload.total_rows} rows
          </p>
        </div>
        <div className="flex items-center gap-2">
          {upload.status === 'COMPLETED' && <span className="status-approved">Completed</span>}
          {upload.status === 'PROCESSING' && (
            <span className="status-reviewed flex items-center gap-1">
              <Clock size={10} /> Processing
            </span>
          )}
          {upload.status === 'FAILED' && <span className="status-rejected">Failed</span>}
          {(upload.status === 'COMPLETED' || upload.status === 'FAILED') && (
            <button className="btn-secondary text-xs py-1" onClick={handleExpand}>
              {expanded ? 'Hide pages' : 'Pages'}
            </button>
          )}
          {upload.status === 'COMPLETED' && (
            <button className="btn-secondary text-xs py-1" onClick={onNavigate}>
              Review
            </button>
          )}
          <button
            className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors disabled:opacity-40"
            onClick={handleDelete}
            disabled={deleting}
            title="Delete upload and all extracted rows"
          >
            {deleting ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
          </button>
        </div>
      </div>

      {/* Page breakdown */}
      {expanded && (
        <div className="bg-gray-50 border-t border-gray-100 px-6 py-3">
          {pages === null ? (
            <p className="text-xs text-gray-400">Loading…</p>
          ) : pages.length === 0 ? (
            <p className="text-xs text-gray-400">No page images found on server.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pages.map((pg) => (
                <div
                  key={pg.page}
                  className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${
                    pg.row_count === 0
                      ? 'border-red-200 bg-red-50 text-red-700'
                      : 'border-green-200 bg-green-50 text-green-700'
                  }`}
                >
                  <span className="font-semibold">Page {pg.page}</span>
                  <span>{pg.row_count} rows</span>
                  {pg.row_count === 0 && (
                    <button
                      className="flex items-center gap-1 ml-1 text-red-600 hover:text-red-800 font-medium disabled:opacity-50"
                      onClick={() => handleRetry(pg.page)}
                      disabled={retrying === pg.page}
                      title="Re-run OCR on this page"
                    >
                      <RefreshCw size={11} className={retrying === pg.page ? 'animate-spin' : ''} />
                      {retrying === pg.page ? 'Retrying…' : 'Retry'}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
