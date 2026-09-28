import { apiFetch, apiFetchBlob } from './client'
import type { DeletedDivision, Division, DivisionPlayer, PlayerStatsRow, ScheduleRow, StandingsRow, Team } from './types'

export function listDivisions(): Promise<Division[]> {
  return apiFetch<Division[]>('/divisions')
}

export function createDivision(body: {
  year: number
  season: string
  age_group: string
  category?: string | null
}): Promise<Division> {
  return apiFetch<Division>('/divisions', { method: 'POST', body: JSON.stringify(body) })
}

export function deleteDivision(divisionId: number): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}`, { method: 'DELETE' })
}

export function restoreDivision(divisionId: number): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}/restore`, { method: 'POST' })
}

export function listDeletedDivisions(): Promise<DeletedDivision[]> {
  return apiFetch<DeletedDivision[]>('/divisions/deleted')
}

export function getPreviousDivision(divisionId: number): Promise<Division | null> {
  return apiFetch<Division | null>(`/divisions/${divisionId}/previous`)
}

export function listDivisionTeams(divisionId: number, includeDeleted = false): Promise<Team[]> {
  return apiFetch<Team[]>(`/divisions/${divisionId}/teams?include_deleted=${includeDeleted}`)
}

export function listDivisionPlayers(divisionId: number): Promise<DivisionPlayer[]> {
  return apiFetch<DivisionPlayer[]>(`/divisions/${divisionId}/players`)
}

export function getStandings(divisionId: number): Promise<StandingsRow[]> {
  return apiFetch<StandingsRow[]>(`/divisions/${divisionId}/standings`)
}

export function getPlayerStats(divisionId: number): Promise<PlayerStatsRow[]> {
  return apiFetch<PlayerStatsRow[]>(`/divisions/${divisionId}/stats`)
}

export function getSeasonGrades(divisionId: number): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>(`/divisions/${divisionId}/season-grades`)
}

export function getSchedule(divisionId: number): Promise<ScheduleRow[]> {
  return apiFetch<ScheduleRow[]>(`/divisions/${divisionId}/schedule`)
}

export function importSchedule(divisionId: number, rows: Record<string, unknown>[]): Promise<{ saved: number }> {
  return apiFetch(`/divisions/${divisionId}/schedule`, { method: 'POST', body: JSON.stringify({ rows }) })
}

export function clearSchedule(divisionId: number): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}/schedule`, { method: 'DELETE' })
}

export async function downloadExportWorkbook(divisionId: number): Promise<void> {
  const { blob, filename } = await apiFetchBlob(`/divisions/${divisionId}/export.xlsx`)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename ?? `division_${divisionId}.xlsx`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
