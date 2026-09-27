import { apiFetch } from './client'
import type { Player, PlayerHistoryEntry } from './types'

export function listPlayers(includeDeleted = false): Promise<Player[]> {
  return apiFetch<Player[]>(`/players?include_deleted=${includeDeleted}`)
}

export function restorePlayer(playerId: number): Promise<void> {
  return apiFetch<void>(`/players/${playerId}/restore`, { method: 'POST' })
}

export function deletePlayer(playerId: number): Promise<void> {
  return apiFetch<void>(`/players/${playerId}`, { method: 'DELETE' })
}

export function getPlayerHistory(playerId: number): Promise<PlayerHistoryEntry[]> {
  return apiFetch<PlayerHistoryEntry[]>(`/players/${playerId}/history`)
}

export function getSiblings(playerId: number): Promise<Player[]> {
  return apiFetch<Player[]>(`/players/${playerId}/siblings`)
}

export function getPosition(playerId: number, divisionId: number, teamId: number): Promise<{ position: string | null }> {
  return apiFetch(`/players/${playerId}/position?division_id=${divisionId}&team_id=${teamId}`)
}

export function setPosition(
  playerId: number,
  body: { division_id: number; team_id: number; position: string }
): Promise<{ position: string | null }> {
  return apiFetch(`/players/${playerId}/position`, { method: 'PUT', body: JSON.stringify(body) })
}

export function getSeasonGrade(playerId: number, divisionId: number): Promise<{ grade: string | null }> {
  return apiFetch(`/players/${playerId}/season-grade?division_id=${divisionId}`)
}

export function setSeasonGrade(
  playerId: number,
  body: { division_id: number; team_id?: number | null; grade: string }
): Promise<{ grade: string | null }> {
  return apiFetch(`/players/${playerId}/season-grade`, { method: 'PUT', body: JSON.stringify(body) })
}
