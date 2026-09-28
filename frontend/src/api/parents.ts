import { apiFetch } from './client'
import type { Parent } from './types'

export function listParents(): Promise<Parent[]> {
  return apiFetch<Parent[]>('/parents')
}

export function setPlayerParent(playerId: number, parentId: number | null): Promise<void> {
  return apiFetch<void>(`/players/${playerId}/parent`, {
    method: 'PUT',
    body: JSON.stringify({ parent_id: parentId }),
  })
}

export function linkSiblings(playerId: number, otherPlayerId: number): Promise<{ parent_id: number }> {
  return apiFetch(`/players/${playerId}/siblings/${otherPlayerId}`, { method: 'POST' })
}
