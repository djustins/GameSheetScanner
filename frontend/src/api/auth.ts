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

export function fetchCurrentUser(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>('/me')
}
