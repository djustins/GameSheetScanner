import { apiFetch } from './client'
import type { Coach, CoachChild, CoachTeam } from './types'

export function listCoaches(): Promise<Coach[]> {
  return apiFetch<Coach[]>('/coaches')
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
