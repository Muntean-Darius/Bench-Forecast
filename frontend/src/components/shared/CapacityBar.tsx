import clsx from 'clsx'

interface CapacityBarProps {
  allocated: number
  capacity: number
  remaining?: number
  showLabels?: boolean
  compact?: boolean
}

export default function CapacityBar({
  allocated,
  capacity,
  remaining,
  showLabels = true,
  compact = false,
}: CapacityBarProps) {
  const safeCapacity = capacity > 0 ? capacity : 8
  const pct = Math.max(0, Math.min(100, (allocated / safeCapacity) * 100))
  const free = remaining ?? Math.max(0, safeCapacity - allocated)
  const isFull = free <= 0.001
  const isOver = allocated > safeCapacity + 0.001

  return (
    <div className="w-full min-w-[130px]">
      {showLabels && (
        <div className="flex items-center justify-between gap-2 text-[10px] font-mono mb-1">
          <span className="text-gray-600 tabnum">
            {allocated.toFixed(1)}h / {safeCapacity.toFixed(1)}h
          </span>
          <span
            className={clsx(
              'tabnum',
              isOver ? 'text-red-600' : isFull ? 'text-yellow-700' : 'text-green-700',
            )}
          >
            {isOver ? 'over capacity' : isFull ? 'full' : `${free.toFixed(1)}h free`}
          </span>
        </div>
      )}
      <div className={clsx('w-full bg-gray-100 overflow-hidden rounded-full', compact ? 'h-1' : 'h-1.5')}>
        <div
          className={clsx(
            'h-full rounded-full transition-all',
            isOver ? 'bg-red-500' : isFull ? 'bg-yellow-500' : 'bg-gray-900',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
