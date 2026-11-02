import { EyeIcon, EyeOffIcon } from '../icons/Icons'

interface CostVisibilityToggleProps {
  visible: boolean
  onToggle: () => void
}

/** Small bordered control that matches the table/toolbar styling. */
export function CostVisibilityToggle({ visible, onToggle }: CostVisibilityToggleProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={visible}
      title={visible ? 'Hide hourly cost rates' : 'Show hourly cost rates'}
      className="inline-flex items-center gap-1.5 rounded border border-gray-200 bg-white px-2.5 py-1.5 font-sans text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-gray-400"
    >
      {visible ? (
        <EyeOffIcon className="h-3.5 w-3.5" />
      ) : (
        <EyeIcon className="h-3.5 w-3.5" />
      )}
      {visible ? 'Hide rates' : 'Show rates'}
    </button>
  )
}
