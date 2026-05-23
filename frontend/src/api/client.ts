/**
 * Typed API client.
 * All requests go through the Vite proxy (/api → http://localhost:8000).
 */

const BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '')

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UploadStatus {
  upload_id: string
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED'
  total_rows: number
  processed_pages: number
  original_filename: string
  error_message: string | null
  created_at: string | null
  completed_at: string | null
}

export interface UploadSummary {
  upload_id: string
  status: string
  total_rows: number
  original_filename: string
  created_at: string | null
}

export interface ExtractedRow {
  id: number
  upload_id: string
  audit_date: string | null
  operation_number: string | null
  engine_number: string | null
  process_name: string | null
  quantity: number | null
  judgement: string | null
  measurements: number[]
  corrected: object | null
  review_status: 'EXTRACTED' | 'REVIEWED' | 'APPROVED' | 'REJECTED'
  row_image_path: string | null
  reviewed_at: string | null
  created_at: string | null
  page: number | null
  gemini_raw: Record<string, unknown> | null
  gpt4o_raw: Record<string, unknown> | null
  agreement_score: number | null
  disagreements: string[]
  confidence_scores?: {
    operation_number?: number
    engine_number?: number
    process_name?: number
    quantity?: number
    audit_date?: number
    judgement?: number
    measurements?: number[]
  }
  unclear_fields?: string[]
}

export interface Operation {
  operation_number: string
  process_name: string | null
  approved_count: number
  nok_count: number
  last_audit_date: string | null
}

export interface AnalyticsRow {
  id: number
  audit_date: string | null
  measurements: number[]
  judgement: string | null
  nominal?: number | null
  upper_limit?: number | null
  lower_limit?: number | null
}

export interface AnalyticsStats {
  total: number
  ok_count: number
  nok_count: number
  ok_pct: number
  nok_pct: number
  avg_torque: number | null
  cp: number | null
  cpk: number | null
}

export interface AnalyticsResponse {
  operation_number: string
  process_name: string | null
  rows: AnalyticsRow[]
  stats: AnalyticsStats
}

export interface ReviewPayload {
  operation_number?: string
  engine_number?: string
  process_name?: string
  quantity?: number
  audit_date?: string
  measurements?: number[]
  judgement?: string
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

export async function uploadPdf(file: File): Promise<{ upload_id: string; filename: string }> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}

export function getUploadStatus(uploadId: string): Promise<UploadStatus> {
  return request<UploadStatus>(`/uploads/${uploadId}/status`)
}

export function listUploads(): Promise<UploadSummary[]> {
  return request<UploadSummary[]>('/uploads')
}

export function uploadFileUrl(uploadId: string): string {
  return `${BASE}/uploads/${uploadId}/file`
}

export interface PageSummary {
  page: number
  row_count: number
  image_path: string
}

export function getUploadPages(uploadId: string): Promise<PageSummary[]> {
  return request<PageSummary[]>(`/uploads/${uploadId}/pages`)
}

export function retryPage(uploadId: string, page: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/uploads/${uploadId}/retry-page/${page}`, { method: 'POST' })
}

export function deleteUpload(uploadId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/uploads/${uploadId}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Review
// ---------------------------------------------------------------------------

export function getUploadRows(uploadId: string): Promise<ExtractedRow[]> {
  return request<ExtractedRow[]>(`/uploads/${uploadId}/rows`)
}

export function reviewRow(id: number, payload: ReviewPayload): Promise<ExtractedRow> {
  return request<ExtractedRow>(`/review-row/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function approveRow(id: number): Promise<ExtractedRow> {
  return request<ExtractedRow>(`/approve-row/${id}`, { method: 'POST' })
}

export function rejectRow(id: number): Promise<ExtractedRow> {
  return request<ExtractedRow>(`/reject-row/${id}`, { method: 'POST' })
}

export function unapproveRow(id: number): Promise<ExtractedRow> {
  return request<ExtractedRow>(`/unapprove-row/${id}`, { method: 'POST' })
}

export function approveAll(uploadId: string): Promise<{ approved: number }> {
  return request<{ approved: number }>(`/uploads/${uploadId}/approve-all`, { method: 'POST' })
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export function listOperations(): Promise<Operation[]> {
  return request<Operation[]>('/operations')
}

export function getAnalytics(
  operationNumber: string,
  startDate?: string,
  endDate?: string,
): Promise<AnalyticsResponse> {
  const params = new URLSearchParams({ operation_number: operationNumber })
  if (startDate) params.set('start_date', startDate)
  if (endDate) params.set('end_date', endDate)
  return request<AnalyticsResponse>(`/analytics?${params.toString()}`)
}

// ---------------------------------------------------------------------------
// Image URL helper
// ---------------------------------------------------------------------------
export function rowImageUrl(imagePath: string): string {
  if (/^https?:\/\//i.test(imagePath)) return imagePath
  // imagePath is an absolute server-side path; serve via /static
  const filename = imagePath.split(/[/\\]/).pop() ?? ''
  return `${BASE}/static/row_images/${filename}`
}

export function isRowCrop(imagePath: string): boolean {
  // Cropped paths contain "_row_" in the filename; full-page paths don't
  return imagePath.includes('_row_')
}
