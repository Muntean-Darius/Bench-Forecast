import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../contexts/AuthContext'

/** Pending CV review badge count for the manager/admin sidebar. */
export function usePendingCvCount(): number {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const [count, setCount] = useState(0)

  const refresh = useCallback(() => {
    if (user?.role !== 'admin' && user?.role !== 'manager') {
      setCount(0)
      return
    }
    api
      .getCvReviews('pending_approval')
      .then((rows) => setCount(Array.isArray(rows) ? rows.length : 0))
      .catch(() => setCount(0))
  }, [user?.role])

  useEffect(() => {
    refresh()
    const onChange = () => refresh()
    window.addEventListener('cv-reviews-changed', onChange)
    const timer = window.setInterval(refresh, 30_000)
    return () => {
      window.removeEventListener('cv-reviews-changed', onChange)
      window.clearInterval(timer)
    }
  }, [refresh, pathname])

  return count
}
