import { apiFetch } from './client'
import type { ApiToken, ApiTokenCreated } from './types'

export function listTokens(): Promise<ApiToken[]> {
  return apiFetch<ApiToken[]>('/tokens')
}

export function createToken(name: string): Promise<ApiTokenCreated> {
  return apiFetch<ApiTokenCreated>('/tokens', { method: 'POST', body: JSON.stringify({ name }) })
}

export function revokeToken(tokenId: number): Promise<void> {
  return apiFetch<void>(`/tokens/${tokenId}`, { method: 'DELETE' })
}
