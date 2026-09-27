import { apiFetch } from './client'
import type { Coach, CoachChild, CoachTeam } from './types'

export function listCoaches(includeDeleted = false): Promise<Coach[]> {
  return apiFetch<Coach[]>(`/coaches?include_deleted=${includeDeleted}`)
}

export function createCoach(body: {
  first_name: string
  last_name?: string | null
  nickname?: string | null
  phone?: string | null
  email?: string | null
}): Promise<Coach> {
  return apiFetch<Coach>('/coaches', { method: 'POST', body: JSON.stringify(body) })
}

export function updateCoach(
  coachId: number,
  body: Partial<{ first_name: string; last_name: string | null; nickname: string | null; phone: string | null; email: string | null }>
): Promise<Coach> {
  return apiFetch<Coach>(`/coaches/${coachId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function deleteCoach(coachId: number): Promise<void> {
  return apiFetch<void>(`/coaches/${coachId}`, { method: 'DELETE' })
}

export function restoreCoach(coachId: number): Promise<void> {
  return apiFetch<void>(`/coaches/${coachId}/restore`, { method: 'POST' })
}

export function getCoachChildrenInDivision(coachId: number, divisionId: number): Promise<CoachChild[]> {
  return apiFetch<CoachChild[]>(`/coaches/${coachId}/children-in-division?division_id=${divisionId}`)
}

export function listCoachChildren(coachId: number): Promise<CoachChild[]> {
  return apiFetch<CoachChild[]>(`/coaches/${coachId}/children`)
}

export function linkCoachChild(coachId: number, playerId: number): Promise<void> {
  return apiFetch<void>(`/coaches/${coachId}/children/${playerId}`, { method: 'POST' })
}

export function unlinkCoachChild(coachId: number, playerId: number): Promise<void> {
  return apiFetch<void>(`/coaches/${coachId}/children/${playerId}`, { method: 'DELETE' })
}

export function listCoachTeams(coachId: number): Promise<CoachTeam[]> {
  return apiFetch<CoachTeam[]>(`/coaches/${coachId}/teams`)
}
