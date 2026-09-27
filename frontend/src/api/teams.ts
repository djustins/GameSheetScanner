import { apiFetch } from './client'
import type { Coach, RosterEntry, Team } from './types'

export function createTeam(body: { division_id: number; name: string }): Promise<Team> {
  return apiFetch<Team>('/teams', { method: 'POST', body: JSON.stringify(body) })
}

export function updateTeam(teamId: number, body: { name?: string; color?: string }): Promise<Team> {
  return apiFetch<Team>(`/teams/${teamId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function deleteTeam(teamId: number): Promise<void> {
  return apiFetch<void>(`/teams/${teamId}`, { method: 'DELETE' })
}

export function listRoster(teamId: number): Promise<RosterEntry[]> {
  return apiFetch<RosterEntry[]>(`/teams/${teamId}/roster`)
}

export function addRosterEntry(
  teamId: number,
  body: { number: string; name: string; player_id?: number | null }
): Promise<RosterEntry> {
  return apiFetch<RosterEntry>(`/teams/${teamId}/roster`, { method: 'POST', body: JSON.stringify(body) })
}

export function updateRosterEntry(
  teamId: number,
  entryId: number,
  body: { number?: string; name?: string; player_id?: number | null }
): Promise<RosterEntry> {
  return apiFetch<RosterEntry>(`/teams/${teamId}/roster/${entryId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function removeRosterEntry(teamId: number, entryId: number): Promise<void> {
  return apiFetch<void>(`/teams/${teamId}/roster/${entryId}`, { method: 'DELETE' })
}

export function listTeamCoaches(teamId: number): Promise<Coach[]> {
  return apiFetch<Coach[]>(`/teams/${teamId}/coaches`)
}

export function assignCoach(teamId: number, coachId: number): Promise<void> {
  return apiFetch<void>(`/teams/${teamId}/coaches/${coachId}`, { method: 'POST' })
}

export function removeCoach(teamId: number, coachId: number): Promise<void> {
  return apiFetch<void>(`/teams/${teamId}/coaches/${coachId}`, { method: 'DELETE' })
}

export function movePlayer(playerId: number, body: { division_id: number; new_team_id: number; note?: string }): Promise<void> {
  return apiFetch<void>(`/players/${playerId}/move`, { method: 'POST', body: JSON.stringify(body) })
}
