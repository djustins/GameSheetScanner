import { apiFetch } from './client'
import type { Evaluation, Player, PlayerHistoryEntry, PlayerMoveNote, PlayerRequest } from './types'

export function listPlayers(includeDeleted = false): Promise<Player[]> {
  return apiFetch<Player[]>(`/players?include_deleted=${includeDeleted}`)
}

export interface PlayerFields {
  first_name: string
  last_name?: string | null
  nickname?: string | null
  birth_date?: string | null
  current_division_id?: number | null
  contact_first_name?: string | null
  contact_last_name?: string | null
  contact_phone?: string | null
  contact_email?: string | null
  usa_ball_hockey_id?: string | null
}

export function createPlayer(fields: PlayerFields): Promise<Player> {
  return apiFetch<Player>('/players', { method: 'POST', body: JSON.stringify(fields) })
}

export function updatePlayer(playerId: number, fields: Partial<PlayerFields>): Promise<Player> {
  return apiFetch<Player>(`/players/${playerId}`, { method: 'PATCH', body: JSON.stringify(fields) })
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

export function listPlayerRequests(playerId: number): Promise<PlayerRequest[]> {
  return apiFetch<PlayerRequest[]>(`/players/${playerId}/requests`)
}

export function addPlayerRequest(
  playerId: number,
  requestedPlayerId: number,
  note?: string,
  hard = false,
): Promise<PlayerRequest> {
  return apiFetch<PlayerRequest>(`/players/${playerId}/requests/${requestedPlayerId}`, {
    method: 'POST',
    body: JSON.stringify({ note: note || null, hard }),
  })
}

export function setPlayerRequestHard(playerId: number, requestId: number, hard: boolean): Promise<PlayerRequest> {
  return apiFetch<PlayerRequest>(`/players/${playerId}/requests/${requestId}`, {
    method: 'PUT',
    body: JSON.stringify({ hard }),
  })
}

export function removePlayerRequest(playerId: number, requestId: number): Promise<void> {
  return apiFetch<void>(`/players/${playerId}/requests/${requestId}`, { method: 'DELETE' })
}

export function getMoveNotes(playerId: number, divisionId: number): Promise<PlayerMoveNote[]> {
  return apiFetch<PlayerMoveNote[]>(`/players/${playerId}/move-notes?division_id=${divisionId}`)
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

export function listEvaluations(playerId: number): Promise<Evaluation[]> {
  return apiFetch<Evaluation[]>(`/players/${playerId}/evaluations`)
}

export function addEvaluation(
  playerId: number,
  body: { division_id: number; team_id?: number | null; grade: string }
): Promise<Evaluation> {
  return apiFetch<Evaluation>(`/players/${playerId}/evaluations`, { method: 'POST', body: JSON.stringify(body) })
}

export function deleteEvaluation(playerId: number, evaluationId: number): Promise<void> {
  return apiFetch<void>(`/players/${playerId}/evaluations/${evaluationId}`, { method: 'DELETE' })
}
