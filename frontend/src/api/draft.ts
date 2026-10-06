import { apiFetch } from './client'
import type {
  AutoDraftResult,
  AutoDraftResults,
  AutoDraftRun,
  DivisionRequest,
  Draft,
  DraftPick,
  DraftPoolPlayer,
  TradeBody,
  TradePreview,
} from './types'

export function getDraft(divisionId: number): Promise<Draft | null> {
  return apiFetch<Draft | null>(`/divisions/${divisionId}/draft`)
}

export function getDraftPool(divisionId: number): Promise<DraftPoolPlayer[]> {
  return apiFetch<DraftPoolPlayer[]>(`/divisions/${divisionId}/draft/pool`)
}

export function getAutoDraftRun(divisionId: number): Promise<AutoDraftRun | null> {
  return apiFetch<AutoDraftRun | null>(`/divisions/${divisionId}/draft/auto-draft-run`)
}

export function startDraft(divisionId: number, teamIdsInOrder: number[]): Promise<Draft> {
  return apiFetch<Draft>(`/divisions/${divisionId}/draft/start`, {
    method: 'POST',
    body: JSON.stringify({ team_ids_in_order: teamIdsInOrder }),
  })
}

export function submitPick(draftId: number, playerId: number): Promise<{ roster_entry_id: number }> {
  return apiFetch(`/drafts/${draftId}/pick`, { method: 'POST', body: JSON.stringify({ player_id: playerId }) })
}

export function undoLastPick(draftId: number): Promise<void> {
  return apiFetch<void>(`/drafts/${draftId}/undo`, { method: 'POST' })
}

export function listPicks(draftId: number): Promise<DraftPick[]> {
  return apiFetch<DraftPick[]>(`/drafts/${draftId}/picks`)
}

export function deleteDraft(draftId: number): Promise<void> {
  return apiFetch<void>(`/drafts/${draftId}`, { method: 'DELETE' })
}

export function autoDraft(divisionId: number): Promise<AutoDraftResult> {
  return apiFetch<AutoDraftResult>(`/divisions/${divisionId}/auto-draft`, { method: 'POST' })
}

export function undoAutoDraft(divisionId: number): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}/auto-draft/undo`, { method: 'POST' })
}

export function storeAutoDraft(divisionId: number): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}/auto-draft/store`, { method: 'POST' })
}

export function getAutoDraftResults(divisionId: number): Promise<AutoDraftResults> {
  return apiFetch<AutoDraftResults>(`/divisions/${divisionId}/draft/auto-draft-results`)
}

export function getDraftNotes(divisionId: number): Promise<{ notes: string }> {
  return apiFetch(`/divisions/${divisionId}/draft/notes`)
}

export function saveDraftNotes(divisionId: number, notes: string): Promise<{ notes: string }> {
  return apiFetch(`/divisions/${divisionId}/draft/notes`, { method: 'PUT', body: JSON.stringify({ notes }) })
}

export function listDivisionRequests(divisionId: number): Promise<DivisionRequest[]> {
  return apiFetch<DivisionRequest[]>(`/divisions/${divisionId}/requests`)
}

export function previewTrade(divisionId: number, body: TradeBody): Promise<TradePreview> {
  return apiFetch<TradePreview>(`/divisions/${divisionId}/trade/preview`, { method: 'POST', body: JSON.stringify(body) })
}

export function trade(divisionId: number, body: TradeBody): Promise<void> {
  return apiFetch<void>(`/divisions/${divisionId}/trade`, { method: 'POST', body: JSON.stringify(body) })
}
