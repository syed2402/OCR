/**
 * Standalone row review card — reusable wrapper that shows the page image
 * alongside a compact read-only summary of an extracted row.
 * (The full editable version lives in ReviewPage.)
 */

import { ExtractedRow, rowSourceImageUrl } from '../api/client'

interface Props {
  row: ExtractedRow
}

export default function RowReviewCard({ row }: Props) {
  return (
    <div className="flex gap-4">
      {/* Image */}
      <div className="w-1/2 bg-gray-100 rounded-lg overflow-hidden flex items-center justify-center min-h-32">
        {row.row_image_path || row.page ? (
          <img
            src={rowSourceImageUrl(row)}
            alt="Row source"
            className="max-w-full max-h-48 object-contain"
          />
        ) : (
          <span className="text-gray-400 text-xs">No image</span>
        )}
      </div>

      {/* Summary */}
      <div className="w-1/2 text-sm space-y-1">
        <p>
          <span className="text-gray-400 text-xs">OPN</span>{' '}
          <span className="font-mono font-medium">{row.operation_number ?? '—'}</span>
        </p>
        <p>
          <span className="text-gray-400 text-xs">Process</span>{' '}
          <span>{row.process_name ?? '—'}</span>
        </p>
        <p>
          <span className="text-gray-400 text-xs">Date</span>{' '}
          <span>{row.audit_date ?? '—'}</span>
        </p>
        <p>
          <span className="text-gray-400 text-xs">Judgement</span>{' '}
          <span
            className={
              row.judgement === 'OK'
                ? 'text-green-600 font-semibold'
                : row.judgement === 'NOK'
                ? 'text-red-600 font-semibold'
                : 'text-gray-500'
            }
          >
            {row.judgement ?? '—'}
          </span>
        </p>
        <p>
          <span className="text-gray-400 text-xs">Measurements</span>{' '}
          <span className="font-mono text-xs">[{row.measurements.join(', ')}]</span>
        </p>
      </div>
    </div>
  )
}
