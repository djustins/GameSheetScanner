import { apiFetch } from './client'

export interface CurrentUser {
  email: string
  display_name: string
  is_admin: boolean
  read_only: boolean
  coach_id: number | null
  pages: string[]
  hide_contact_details: boolean
}

export interface LoginResponse {
  token: string
  user: CurrentUser
}

export function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function updateMyProfile(body: { display_name: string }): Promise<CurrentUser> {
  return apiFetch<CurrentUser>('/me', { method: 'PATCH', body: JSON.stringify(body) })
}

export function changeMyPassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiFetch<void>('/me/password', {
    method: 'PUT',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
}

export function fetchCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>('/me')
}
