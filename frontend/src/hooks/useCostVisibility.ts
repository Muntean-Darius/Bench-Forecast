import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

const STORAGE_KEY = 'bf_show_cost_rates'

/**
 * Session-scoped visibility for employee hourly cost rates.
 *
 * Rates are hidden by default, including on a fresh login/session. Only
 * admins and managers can reveal them; the preference survives navigation
 * within the current browser session (sessionStorage).
 */
export function useCostVisibility() {
  const { user } = useAuth()
  const canView = user?.role === 'admin' || user?.role === 'manager'

  const [requested, setRequested] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  const visible = canView && requested

  function toggle() {
    if (!canView) return
    setRequested((prev) => {
      const next = !prev
      try {
        sessionStorage.setItem(STORAGE_KEY, next ? '1' : '0')
      } catch {
        // storage can be unavailable in private modes; the state still works
      }
      return next
    })
  }

  return { visible, toggle, canView }
}

/** `€123.45` or `€***` for an hourly rate. */
export function maskCost(
  value: number | null | undefined,
  visible: boolean,
  decimals = 2,
): string {
  return visible ? `€${Number(value ?? 0).toFixed(decimals)}` : '€***'
}

/** `€1,234` or `€***` for monthly/aggregate amounts. */
export function maskAmount(
  value: number | null | undefined,
  visible: boolean,
  decimals = 0,
): string {
  if (!visible) return '€***'
  return `€${Number(value ?? 0).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}
