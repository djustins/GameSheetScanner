import { apiFetch } from './client'

// An incident or an upcoming maintenance window a service has posted.
export interface ServiceNotice {
  name: string | null
  status: string | null
  impact: string | null
  url: string | null
  updated_at: string | null
  scheduled_for: string | null
  scheduled_until: string | null
  body: string | null
}

export interface ServiceProvider {
  key: string
  name: string
  // What it does for this app.
  role: string
  status_url: string
  dashboard_url: string
  // The service's own summary: none (fine), minor, major, critical, maintenance, or unknown.
  indicator: string
  description: string
  incidents: ServiceNotice[]
  maintenances: ServiceNotice[]
  degraded: string[]
}

export interface ServiceCheck {
  name: string
  state: 'ok' | 'warning' | 'problem' | 'unknown'
  detail: string
}

export interface ServicesSnapshot {
  checked_at: string
  providers: ServiceProvider[]
  checks: ServiceCheck[]
}

// Admin-only. Results are kept for two minutes on the server unless `refresh`.
export function getServices(refresh: boolean): Promise<ServicesSnapshot> {
  return apiFetch(`/services${refresh ? '?refresh=true' : ''}`)
}
