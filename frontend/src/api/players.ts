import { apiFetch } from './client'
import type { Player, PlayerHistoryEntry } from './types'

export function listPlayers(): Promise<Player[]> {
  return apiFetch<Player[]>('/players')
}

export function getPlayerHistory(playerId: number): Promise<PlayerHistoryEntry[]> {
  return apiFetch<PlayerHistoryEntry[]>(`/players/${playerId}/history`)
}
