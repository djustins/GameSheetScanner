import { useState } from 'react'
import {
  Accordion,
  Button,
  Checkbox,
  Code,
  Group,
  Modal,
  MultiSelect,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRole,
  createUser,
  deactivateUser,
  deleteRole,
  listRoles,
  listUsers,
  restoreUser,
  setUserPassword,
  updateRole,
  updateUser,
} from '../api/users'
import { listCoaches } from '../api/coaches'
import type { Role, User } from '../api/types'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { PAGES } from '../utils/pages'

function generatePassword(): string {
  return crypto.randomUUID().replace(/-/g, '').slice(0, 12)
}

function RoleRow({ role }: { role: Role }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(role.name)
  const [pages, setPages] = useState(role.pages)
  const [readOnly, setReadOnly] = useState(role.read_only)
  const [hideContact, setHideContact] = useState(role.hide_contact_details)

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const saveMutation = useMutation({
    mutationFn: () =>
      updateRole(role.id, { name, pages, read_only: readOnly, hide_contact_details: hideContact }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      notifications.show({ message: 'Saved.' })
    },
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteRole(role.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roles'] }),
    onError,
  })

  return (
    <Accordion.Item value={String(role.id)}>
      <Accordion.Control>
        {role.name} ({role.pages.length} page{role.pages.length !== 1 ? 's' : ''})
      </Accordion.Control>
      <Accordion.Panel>
        <Stack>
          <TextInput label="Role name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <MultiSelect
            label="Pages"
            data={Object.entries(PAGES).map(([value, label]) => ({ value, label }))}
            value={pages}
            onChange={setPages}
          />
          <Checkbox
            label="Read-only (can view its pages but not save/create/delete)"
            checked={readOnly}
            onChange={(e) => setReadOnly(e.currentTarget.checked)}
          />
          <Checkbox
            label="Hide contact details (parent name still visible, phone/email hidden)"
            checked={hideContact}
            onChange={(e) => setHideContact(e.currentTarget.checked)}
          />
          <Group>
            <Button loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              Save
            </Button>
            <Button
              color="red"
              variant="outline"
              onClick={() =>
                confirm(`Delete role "${role.name}"? Anyone with it loses page access.`) && deleteMutation.mutate()
              }
            >
              Delete role
            </Button>
          </Group>
        </Stack>
      </Accordion.Panel>
    </Accordion.Item>
  )
}

function UserRow({ rowUser, roles, isSelf, lastAdmin }: { rowUser: User; roles: Role[]; isSelf: boolean; lastAdmin: boolean }) {
  const queryClient = useQueryClient()
  const [displayName, setDisplayName] = useState(rowUser.display_name ?? '')
  const [isAdmin, setIsAdmin] = useState(rowUser.is_admin)
  const [roleId, setRoleId] = useState<string | null>(rowUser.role_id ? String(rowUser.role_id) : null)
  const [coachId, setCoachId] = useState<string | null>(rowUser.coach_id ? String(rowUser.coach_id) : null)
  const [newPassword, setNewPassword] = useState<string | null>(null)

  const { data: coaches } = useQuery({ queryKey: ['coaches'], queryFn: () => listCoaches() })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  const saveMutation = useMutation({
    mutationFn: () =>
      updateUser(rowUser.id, {
        display_name: displayName,
        is_admin: isAdmin,
        role_id: roleId ? Number(roleId) : null,
        coach_id: coachId ? Number(coachId) : null,
      }),
    onSuccess: () => {
      invalidate()
      notifications.show({ message: 'Saved.' })
    },
    onError,
  })

  const applyPasswordMutation = useMutation({
    mutationFn: () => setUserPassword(rowUser.id, newPassword!),
    onSuccess: () => {
      setNewPassword(null)
      notifications.show({ message: "Password updated — share it with them directly; it won't be shown again." })
    },
    onError,
  })

  const deactivateMutation = useMutation({
    mutationFn: () => deactivateUser(rowUser.id),
    onSuccess: invalidate,
    onError,
  })

  return (
    <Accordion.Item value={String(rowUser.id)}>
      <Accordion.Control>
        {(rowUser.display_name || rowUser.email) + ` — ${rowUser.email}` + (rowUser.is_admin ? ' (admin)' : '')}
      </Accordion.Control>
      <Accordion.Panel>
        <Stack>
          <TextInput label="Display name" value={displayName} onChange={(e) => setDisplayName(e.currentTarget.value)} />
          <Checkbox
            label="Admin (full access, including User Management)"
            checked={isAdmin}
            disabled={lastAdmin}
            onChange={(e) => setIsAdmin(e.currentTarget.checked)}
          />
          <Select
            label="Role"
            placeholder="— No role (no page access) —"
            data={roles.map((r) => ({ value: String(r.id), label: r.name }))}
            value={roleId}
            onChange={setRoleId}
            disabled={isAdmin}
            clearable
          />
          <Select
            label="Linked coach profile"
            placeholder="— None —"
            data={(coaches ?? []).map((c) => ({ value: String(c.id), label: c.name }))}
            value={coachId}
            onChange={setCoachId}
            searchable
            clearable
          />
          <Group>
            <Button loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              Save
            </Button>
            <Button variant="outline" onClick={() => setNewPassword(generatePassword())}>
              Generate new password
            </Button>
            <Button color="red" variant="outline" disabled={lastAdmin} onClick={() => deactivateMutation.mutate()}>
              Deactivate
            </Button>
          </Group>
          {newPassword && (
            <Group>
              <Code>{newPassword}</Code>
              <Button size="xs" loading={applyPasswordMutation.isPending} onClick={() => applyPasswordMutation.mutate()}>
                Apply this password
              </Button>
            </Group>
          )}
          {isSelf && rowUser.is_admin && lastAdmin && (
            <Text size="xs" c="dimmed">
              Can't remove the last admin's own admin access.
            </Text>
          )}
        </Stack>
      </Accordion.Panel>
    </Accordion.Item>
  )
}

export function UserManagementPage() {
  const { user: currentUser } = useAuth()
  const queryClient = useQueryClient()
  const [roleModalOpen, setRoleModalOpen] = useState(false)
  const [userModalOpen, setUserModalOpen] = useState(false)

  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: listRoles })
  const { data: activeUsers } = useQuery({ queryKey: ['users'], queryFn: () => listUsers() })
  const { data: allUsers } = useQuery({ queryKey: ['users', 'all'], queryFn: () => listUsers(true) })
  const deactivatedUsers = (allUsers ?? []).filter((u) => u.deleted_at)

  const activeAdminCount = (activeUsers ?? []).filter((u) => u.is_admin).length

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const [newRoleName, setNewRoleName] = useState('')
  const [newRolePages, setNewRolePages] = useState<string[]>([])
  const [newRoleReadOnly, setNewRoleReadOnly] = useState(false)
  const [newRoleHideContact, setNewRoleHideContact] = useState(false)

  const createRoleMutation = useMutation({
    mutationFn: () =>
      createRole({ name: newRoleName.trim(), pages: newRolePages, read_only: newRoleReadOnly, hide_contact_details: newRoleHideContact }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      setRoleModalOpen(false)
      setNewRoleName('')
      setNewRolePages([])
      setNewRoleReadOnly(false)
      setNewRoleHideContact(false)
    },
    onError,
  })

  const [newUserEmail, setNewUserEmail] = useState('')
  const [newUserName, setNewUserName] = useState('')
  const [newUserPassword, setNewUserPassword] = useState('')
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false)
  const [newUserRoleId, setNewUserRoleId] = useState<string | null>(null)

  const createUserMutation = useMutation({
    mutationFn: () =>
      createUser({
        email: newUserEmail.trim(),
        password: newUserPassword,
        display_name: newUserName.trim() || null,
        is_admin: newUserIsAdmin,
        role_id: newUserRoleId ? Number(newUserRoleId) : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setUserModalOpen(false)
      setNewUserEmail('')
      setNewUserName('')
      setNewUserPassword('')
      setNewUserIsAdmin(false)
      setNewUserRoleId(null)
    },
    onError,
  })

  const restoreMutation = useMutation({
    mutationFn: (userId: number) => restoreUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
    onError,
  })

  return (
    <Stack>
      <Title order={2}>User Management</Title>
      <Text size="sm" c="dimmed">
        Admins always see every page, including this one, no matter what role they have. Everyone else sees
        whatever pages their assigned role grants.
      </Text>

      <Group justify="space-between">
        <Title order={4}>Roles</Title>
        <Button size="xs" onClick={() => setRoleModalOpen(true)}>
          Add Role
        </Button>
      </Group>
      {!roles || roles.length === 0 ? (
        <Text size="sm" c="dimmed">
          No roles yet — create one, then assign it to users.
        </Text>
      ) : (
        <Accordion variant="separated">
          {roles.map((role) => (
            <RoleRow key={role.id} role={role} />
          ))}
        </Accordion>
      )}

      <Group justify="space-between" mt="lg">
        <Title order={4}>Users</Title>
        <Button size="xs" onClick={() => setUserModalOpen(true)}>
          Add User
        </Button>
      </Group>
      <Accordion variant="separated">
        {(activeUsers ?? []).map((u) => (
          <UserRow
            key={u.id}
            rowUser={u}
            roles={roles ?? []}
            isSelf={u.email === currentUser?.email}
            lastAdmin={u.email === currentUser?.email && u.is_admin && activeAdminCount <= 1}
          />
        ))}
      </Accordion>

      {deactivatedUsers.length > 0 && (
        <>
          <Title order={5} mt="md">
            Deactivated users ({deactivatedUsers.length})
          </Title>
          <Stack gap="xs">
            {deactivatedUsers.map((u) => (
              <Group key={u.id} justify="space-between">
                <Text size="sm">{u.email}</Text>
                <Button size="xs" variant="subtle" onClick={() => restoreMutation.mutate(u.id)}>
                  Reactivate
                </Button>
              </Group>
            ))}
          </Stack>
        </>
      )}

      <Modal opened={roleModalOpen} onClose={() => setRoleModalOpen(false)} title="Add Role">
        <Stack>
          <TextInput label="Role name" value={newRoleName} onChange={(e) => setNewRoleName(e.currentTarget.value)} />
          <MultiSelect
            label="Pages"
            data={Object.entries(PAGES).map(([value, label]) => ({ value, label }))}
            value={newRolePages}
            onChange={setNewRolePages}
          />
          <Checkbox
            label="Read-only (can view its pages but not save/create/delete)"
            checked={newRoleReadOnly}
            onChange={(e) => setNewRoleReadOnly(e.currentTarget.checked)}
          />
          <Checkbox
            label="Hide contact details (parent name still visible, phone/email hidden)"
            checked={newRoleHideContact}
            onChange={(e) => setNewRoleHideContact(e.currentTarget.checked)}
          />
          <Button disabled={!newRoleName.trim()} loading={createRoleMutation.isPending} onClick={() => createRoleMutation.mutate()}>
            Create Role
          </Button>
        </Stack>
      </Modal>

      <Modal opened={userModalOpen} onClose={() => setUserModalOpen(false)} title="Add User">
        <Stack>
          <TextInput label="Email" value={newUserEmail} onChange={(e) => setNewUserEmail(e.currentTarget.value)} required />
          <TextInput
            label="Display name (optional)"
            value={newUserName}
            onChange={(e) => setNewUserName(e.currentTarget.value)}
          />
          <Group align="flex-end">
            <TextInput
              label="Temporary password"
              value={newUserPassword}
              onChange={(e) => setNewUserPassword(e.currentTarget.value)}
              flex={1}
              description="There's no email delivery — share this with them directly."
            />
            <Button variant="light" onClick={() => setNewUserPassword(generatePassword())}>
              Generate
            </Button>
          </Group>
          <Checkbox
            label="Admin (full access)"
            checked={newUserIsAdmin}
            onChange={(e) => setNewUserIsAdmin(e.currentTarget.checked)}
          />
          <Select
            label="Role"
            placeholder="— No role (no page access) —"
            data={(roles ?? []).map((r) => ({ value: String(r.id), label: r.name }))}
            value={newUserRoleId}
            onChange={setNewUserRoleId}
            disabled={newUserIsAdmin}
            clearable
          />
          <Button
            disabled={!newUserEmail.trim() || !newUserEmail.includes('@') || !newUserPassword}
            loading={createUserMutation.isPending}
            onClick={() => createUserMutation.mutate()}
          >
            Create User
          </Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
