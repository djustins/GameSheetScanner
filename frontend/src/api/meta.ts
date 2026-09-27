import { apiFetch } from './client'

export function getAgeGroups(): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>('/age-groups')
}
