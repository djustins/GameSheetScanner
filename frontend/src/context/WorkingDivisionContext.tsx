import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import { getWorkingDivision, setWorkingDivision } from '../api/settings'
import type { Division } from '../api/types'
import { useAuth } from '../auth/AuthContext'

interface WorkingDivisionContextValue {
  workingDivisionId: number | null
  setWorkingDivisionId: (id: number | null) => void
  divisions: Division[]
  loading: boolean
}

const WorkingDivisionContext = createContext<WorkingDivisionContextValue | undefined>(undefined)

export function WorkingDivisionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [workingDivisionId, setLocalWorkingDivisionId] = useState<number | null>(null)
  const [initialized, setInitialized] = useState(false)

  const { data: divisions, isLoading: divisionsLoading } = useQuery({
    queryKey: ['divisions'],
    queryFn: listDivisions,
    enabled: !!user,
  })

  const { data: saved, isLoading: savedLoading } = useQuery({
    queryKey: ['working-division'],
    queryFn: getWorkingDivision,
    enabled: !!user,
  })

  useEffect(() => {
    if (initialized || saved === undefined) return
    setLocalWorkingDivisionId(saved.division_id)
    setInitialized(true)
  }, [saved, initialized])

  function setWorkingDivisionId(id: number | null) {
    setLocalWorkingDivisionId(id)
    setWorkingDivision(id).then(() => {
      queryClient.invalidateQueries({ queryKey: ['working-division'] })
    })
  }

  return (
    <WorkingDivisionContext.Provider
      value={{
        workingDivisionId,
        setWorkingDivisionId,
        divisions: divisions ?? [],
        loading: divisionsLoading || savedLoading,
      }}
    >
      {children}
    </WorkingDivisionContext.Provider>
  )
}

export function useWorkingDivision(): WorkingDivisionContextValue {
  const ctx = useContext(WorkingDivisionContext)
  if (!ctx) {
    throw new Error('useWorkingDivision must be used within a WorkingDivisionProvider')
  }
  return ctx
}
