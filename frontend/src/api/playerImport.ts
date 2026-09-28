import { apiFetchMultipart, apiFetch } from './client'
import type { PlayerImportEntry, PlayerImportPlanResponse, PlayerImportResult } from './types'

export function getImportPlan(divisionId: number, file: File): Promise<PlayerImportPlanResponse> {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetchMultipart<PlayerImportPlanResponse>(`/divisions/${divisionId}/players/import-plan`, formData)
}

export function applyImportPlan(divisionId: number, plan: PlayerImportEntry[]): Promise<PlayerImportResult> {
  return apiFetch<PlayerImportResult>(`/divisions/${divisionId}/players/import-apply`, {
    method: 'POST',
    body: JSON.stringify({ plan }),
  })
}
