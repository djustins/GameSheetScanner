import { apiFetch } from './client'
import type { Division, DivisionPlayer, PlayerStatsRow, StandingsRow, Team } from './types'

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

export function listDivisionTeams(divisionId: number): Promise<Team[]> {
  return apiFetch<Team[]>(`/divisions/${divisionId}/teams`)
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
