import { apiFetch } from './client'

export function getWorkingDivision(): Promise<{ division_id: number | null }> {
  return apiFetch('/settings/working-division')
}

export function setWorkingDivision(divisionId: number | null): Promise<{ division_id: number | null }> {
  return apiFetch('/settings/working-division', {
    method: 'PUT',
    body: JSON.stringify({ division_id: divisionId }),
  })
}
