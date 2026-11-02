import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

export type UserRole = 'admin' | 'manager' | 'employee'

export interface User {
  id: string
  username: string
  role: UserRole
  full_name: string
  employee_id: string | null
}

interface AuthContextType {
  user: User | null
  token: string | null
  login: (username: string, password: string) => Promise<boolean>
  logout: () => void
  isLoading: boolean
}

const AuthContext = createContext<AuthContextType | null>(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  // Per-tab sessions: the JWT lives in sessionStorage so multiple tabs can be
  // logged in as DIFFERENT users simultaneously. localStorage would share one
  // token across every tab — logging in as another user in one tab silently
  // re-identifies all other tabs on their next refresh.
  const [token, setToken] = useState<string | null>(sessionStorage.getItem('bf_token'))
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (token) {
      // Validate token on mount
      fetch('/api/v1/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(res => {
          if (!res.ok) throw new Error('Invalid token')
          return res.json()
        })
        .then(userData => {
          setUser(userData)
          setIsLoading(false)
        })
        .catch(() => {
          sessionStorage.removeItem('bf_token')
          sessionStorage.removeItem('bf_show_cost_rates')
          setToken(null)
          setUser(null)
          setIsLoading(false)
        })
    } else {
      setIsLoading(false)
    }
  }, [token])

  const login = async (username: string, password: string): Promise<boolean> => {
    try {
      const res = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) return false
      const data = await res.json()
      sessionStorage.setItem('bf_token', data.token)
      setToken(data.token)
      setUser(data.user)
      return true
    } catch {
      return false
    }
  }

  const logout = () => {
    sessionStorage.removeItem('bf_token')
    sessionStorage.removeItem('bf_show_cost_rates')
    setToken(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, token, login, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  )
}
