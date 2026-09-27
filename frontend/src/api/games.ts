import { apiFetch, apiFetchMultipart } from './client'
import type { ExtractResult, GameData, GameListRow } from './types'

export function extractGameSheet(file: File): Promise<ExtractResult[]> {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetchMultipart<ExtractResult[]>('/game-sheets/extract', formData)
}

export function listGames(divisionId: number): Promise<GameListRow[]> {
  return apiFetch<GameListRow[]>(`/divisions/${divisionId}/games`)
}

export function createGame(
  data: GameData,
  sourceFile: string,
  divisionId: number
): Promise<{ id: number; already_existed: boolean }> {
  return apiFetch(`/games`, {
    method: 'POST',
    body: JSON.stringify({ data, source_file: sourceFile, division_id: divisionId }),
  })
}

export function getGame(gameId: number): Promise<{ data: GameData; source_file: string }> {
  return apiFetch(`/games/${gameId}`)
}

export function updateGame(gameId: number, data: GameData, divisionId: number): Promise<{ id: number }> {
  return apiFetch(`/games/${gameId}`, {
    method: 'PATCH',
    body: JSON.stringify({ data, division_id: divisionId }),
  })
}

export function deleteGame(gameId: number): Promise<void> {
  return apiFetch<void>(`/games/${gameId}`, { method: 'DELETE' })
}
