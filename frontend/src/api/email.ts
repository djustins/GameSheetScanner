import { apiFetch } from './client'

export interface EmailAudience {
  division_id: number
  // Empty = the whole division.
  team_ids: number[]
  parents: boolean
  coaches: boolean
}

export interface EmailRecipient {
  email: string
  name: string
  role: string
  // Whose parent they are, or which team they coach.
  about: string
}

export interface EmailRecipients {
  recipients: EmailRecipient[]
  // People the message was meant for who have no email on file.
  missing: string[]
}

export interface EmailSendResult {
  sent: number
  failed: number
  error: string | null
}

export interface EmailLogRow {
  id: number
  kind: 'message' | 'test' | 'invite' | 'password_reset' | 'notice'
  subject: string
  // Hidden for account emails, whose text contains a sign-in link.
  body: string | null
  audience: string | null
  recipients: string[]
  sent: number
  failed: number
  error: string | null
  created_at: string
  sent_by: string | null
  division: string | null
}

export function getEmailStatus(): Promise<{ configured: boolean; from: string | null }> {
  return apiFetch('/email/status')
}

export function previewRecipients(audience: EmailAudience): Promise<EmailRecipients> {
  return apiFetch('/email/recipients', { method: 'POST', body: JSON.stringify(audience) })
}

export function sendEmail(
  message: EmailAudience & { subject: string; body: string; test?: boolean }
): Promise<EmailSendResult> {
  return apiFetch('/email/send', { method: 'POST', body: JSON.stringify(message) })
}

export function getEmailLog(): Promise<EmailLogRow[]> {
  return apiFetch('/email/log')
}

// Always succeeds, whether or not the address has an account.
export function requestPasswordReset(email: string): Promise<void> {
  return apiFetch<void>('/password-reset/request', { method: 'POST', body: JSON.stringify({ email }) })
}

export function confirmPasswordReset(token: string, password: string): Promise<void> {
  return apiFetch<void>('/password-reset/confirm', { method: 'POST', body: JSON.stringify({ token, password }) })
}

// Emails this user a link to choose their own password.
export function inviteUser(userId: number): Promise<{ sent_to: string }> {
  return apiFetch(`/users/${userId}/invite`, { method: 'POST' })
}
