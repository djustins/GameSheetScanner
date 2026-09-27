import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function AdminRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth()

  if (!user?.is_admin) {
    return <Navigate to="/divisions" replace />
  }

  return <>{children}</>
}
