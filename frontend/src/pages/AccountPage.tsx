import { useState } from 'react'
import { Alert, Badge, Button, Divider, Group, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { changeMyPassword, updateMyProfile } from '../api/auth'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ApiTokensPage } from './ApiTokensPage'

const onError = (err: unknown) =>
  notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

/** Everything about the signed-in person's own account: who they're signed in
 * as, their name, their password, and their API tokens. */
export function AccountPage() {
  const { user, logout, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [name, setName] = useState(user?.display_name ?? '')
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')

  const nameMutation = useMutation({
    mutationFn: () => updateMyProfile({ display_name: name.trim() }),
    onSuccess: async () => {
      await refreshUser()
      notifications.show({ color: 'green', message: 'Name saved.' })
    },
    onError,
  })

  const mismatch = again !== '' && next !== again
  const passwordMutation = useMutation({
    mutationFn: () => changeMyPassword(current, next),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      setAgain('')
      notifications.show({ color: 'green', message: 'Password changed. Use the new one next time you sign in.' })
    },
    onError,
  })

  if (!user) return null
  const access = user.is_admin
    ? 'Admin: every page, and can edit everything.'
    : `${user.pages.length} page${user.pages.length === 1 ? '' : 's'}${user.read_only ? ', view only' : ''}. An admin sets this in User Management.`

  return (
    <Stack ta="left" maw={720} mx="auto">
      <Title order={2} ta="center">
        My Account
      </Title>

      <Paper withBorder p="lg" radius="md">
        <Stack>
          <Group justify="space-between">
            <Title order={4}>Profile</Title>
            {user.is_admin && <Badge>Admin</Badge>}
          </Group>
          <TextInput
            label="Sign-in email"
            description="This is what you sign in with. An admin can change it."
            value={user.email}
            readOnly
          />
          <Group align="flex-end">
            <TextInput
              flex={1}
              label="Your name"
              description="Shown at the top of the app and on anything you send."
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
            />
            <Button
              disabled={name.trim() === '' || name.trim() === (user.display_name ?? '')}
              loading={nameMutation.isPending}
              onClick={() => nameMutation.mutate()}
            >
              Save name
            </Button>
          </Group>
          <Text size="sm" c="dimmed">
            Access: {access}
          </Text>
        </Stack>
      </Paper>

      {user.is_admin && (
        <Paper withBorder p="lg" radius="md">
          <Group justify="space-between">
            <div>
              <Title order={4}>Email parents and coaches</Title>
              <Text size="sm" c="dimmed">
                Write a message to a division or to particular teams, and see what has been sent.
              </Text>
            </div>
            <Button variant="default" onClick={() => navigate('/admin/email')}>
              Open Email
            </Button>
          </Group>
        </Paper>
      )}

      <Paper withBorder p="lg" radius="md">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (!mismatch) passwordMutation.mutate()
          }}
        >
          <Stack>
            <Title order={4}>Password</Title>
            <PasswordInput
              label="Current password"
              value={current}
              onChange={(e) => setCurrent(e.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <PasswordInput
              label="New password"
              description="At least 8 characters."
              value={next}
              onChange={(e) => setNext(e.currentTarget.value)}
              autoComplete="new-password"
              required
            />
            <PasswordInput
              label="New password again"
              value={again}
              onChange={(e) => setAgain(e.currentTarget.value)}
              error={mismatch ? "The two new passwords don't match." : undefined}
              autoComplete="new-password"
              required
            />
            <Group>
              <Button type="submit" disabled={mismatch} loading={passwordMutation.isPending}>
                Change password
              </Button>
              <Text size="sm" c="dimmed">
                Forgotten it? Sign out and use &quot;Forgot your password?&quot; on the sign-in page.
              </Text>
            </Group>
          </Stack>
        </form>
      </Paper>

      <Paper withBorder p="lg" radius="md">
        <ApiTokensPage embedded />
      </Paper>

      <Divider />
      <Group justify="space-between">
        <Alert color="gray" p="xs" style={{ flex: 1 }}>
          Signed in as {user.email}.
        </Alert>
        <Button color="red" variant="outline" onClick={logout}>
          Sign out
        </Button>
      </Group>
    </Stack>
  )
}
