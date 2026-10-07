import { apiFetch } from './client'
import type { Role, UsageSummary, User } from './types'

export function recordPageView(path: string): Promise<void> {
  return apiFetch<void>('/me/page-views', { method: 'POST', body: JSON.stringify({ path }) })
}

export function getUsage(days: number): Promise<UsageSummary> {
  return apiFetch<UsageSummary>(`/usage?days=${days}`)
}

export function listRoles(): Promise<Role[]> {
  return apiFetch<Role[]>('/roles')
}

export function createRole(body: {
  name: string
  pages: string[]
  read_only: boolean
  hide_contact_details: boolean
}): Promise<Role> {
  return apiFetch<Role>('/roles', { method: 'POST', body: JSON.stringify(body) })
}

export function updateRole(
  roleId: number,
  body: Partial<{ name: string; pages: string[]; read_only: boolean; hide_contact_details: boolean }>
): Promise<Role> {
  return apiFetch<Role>(`/roles/${roleId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function deleteRole(roleId: number): Promise<void> {
  return apiFetch<void>(`/roles/${roleId}`, { method: 'DELETE' })
}

export function listUsers(includeDeleted = false): Promise<User[]> {
  return apiFetch<User[]>(`/users?include_deleted=${includeDeleted}`)
}

export function createUser(body: {
  email: string
  password: string
  display_name?: string | null
  is_admin: boolean
  role_id: number | null
}): Promise<User> {
  return apiFetch<User>('/users', { method: 'POST', body: JSON.stringify(body) })
}

export function updateUser(
  userId: number,
  body: Partial<{ display_name: string; is_admin: boolean; role_id: number | null; coach_id: number | null }>
): Promise<User> {
  return apiFetch<User>(`/users/${userId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function setUserPassword(userId: number, password: string): Promise<void> {
  return apiFetch<void>(`/users/${userId}/password`, { method: 'PUT', body: JSON.stringify({ password }) })
}

export function deactivateUser(userId: number): Promise<void> {
  return apiFetch<void>(`/users/${userId}`, { method: 'DELETE' })
}

export function restoreUser(userId: number): Promise<void> {
  return apiFetch<void>(`/users/${userId}/restore`, { method: 'POST' })
}
