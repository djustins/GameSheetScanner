import type { ReactNode } from 'react'
import { Alert } from '@mantine/core'
import { useAuth } from './AuthContext'

// Mirrors app.py's visible_pages / is_read_only / hide_contact_details: an admin sees
// every page and all contact details; anyone else only the page keys their
// role grants (game_sheet_core.PAGES). UI-only -- the API itself doesn't
// enforce either (see api.py's module docstring).
export function useAccess() {
  const { user } = useAuth()
  const canView = (pageKey: string) => !!user && (user.is_admin || user.pages.includes(pageKey))
  return {
    canView,
    canViewAny: (pageKeys: string[]) => pageKeys.some(canView),
    hideContactDetails: !!user && !user.is_admin && user.hide_contact_details,
    readOnly: !!user && !user.is_admin && user.read_only,
  }
}

export function NoAccess() {
  return (
    <Alert color="yellow" title="No access">
      You don&apos;t have access to this page. Ask an admin to grant it in User Management.
    </Alert>
  )
}

export function PageAccess({ pages, children }: { pages: string[]; children: ReactNode }) {
  const { canViewAny } = useAccess()
  return canViewAny(pages) ? <>{children}</> : <NoAccess />
}

// Disables every input/button inside it for a read-only role, via a native
// disabled <fieldset> (display: contents, so it doesn't affect layout).
// Wrap editing areas only -- tabs, search boxes and other navigation inside
// one would be disabled too. Portaled content (Drawer/Modal bodies) isn't a
// DOM descendant, so wrap inside those rather than around them.
export function Writable({ children }: { children: ReactNode }) {
  const { readOnly } = useAccess()
  return (
    <fieldset disabled={readOnly} style={{ display: 'contents', border: 0, margin: 0, padding: 0 }}>
      {children}
    </fieldset>
  )
}

export function ReadOnlyNotice() {
  const { readOnly } = useAccess()
  if (!readOnly) return null
  return (
    <Alert color="gray" variant="light">
      🔒 Your role is read-only — editing is disabled.
    </Alert>
  )
}
