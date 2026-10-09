import { apiFetch, apiFetchBlob } from './client'
import type {
  DeletedDivision,
  Division,
  DivisionEvaluations,
  DivisionPlayer,
  PlayerStatsRow,
  ScheduleRow,
  StandingsRow,
  Team,
  TeamOverview,
} from './types'

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
  // Revoke after the browser has started the download, not before.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export interface SyncSchedule {
  // 0 = off; 1, 2, 3, 4, 6, 12 = that often; 24 = once a day in the hour daily_hour.
  every_hours: number
  // 0-23, Pittsburgh time.
  daily_hour: number
  // When the schedule last ran a sync, or null.
  last_run: string | null
}

export function getSyncSchedule(): Promise<SyncSchedule> {
  return apiFetch('/league-site/schedule')
}

export function setSyncSchedule(body: { every_hours: number; daily_hour?: number }): Promise<SyncSchedule> {
  return apiFetch('/league-site/schedule', { method: 'PUT', body: JSON.stringify(body) })
}

// Syncs every current league from the league site right now; one report per league.
export function syncLeagueSite(): Promise<{ league: string; skipped: string | null; added: string[]; failed: string[] }[]> {
  return apiFetch('/league-site/sync', { method: 'POST' })
}

// When this division was last brought up to date from the league stats site.
export function getLeagueSync(divisionId: number): Promise<{ synced_at: string | null }> {
  return apiFetch(`/divisions/${divisionId}/league-sync`)
}

export function getTeamsOverview(divisionId: number): Promise<TeamOverview[]> {
  return apiFetch<TeamOverview[]>(`/divisions/${divisionId}/teams-overview`)
}

export function getDivisionEvaluations(divisionId: number): Promise<DivisionEvaluations> {
  return apiFetch<DivisionEvaluations>(`/divisions/${divisionId}/evaluations`)
}
