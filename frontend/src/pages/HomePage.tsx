import { Button, Group, Paper, Stack, Text, Title } from '@mantine/core'
import { useAuth } from '../auth/AuthContext'

export function HomePage() {
  const { user, logout } = useAuth()

  return (
    <Stack p="xl" maw={600} mx="auto">
      <Group justify="space-between">
        <Title order={2}>GameSheetScanner</Title>
        <Button variant="subtle" onClick={logout}>
          Log out
        </Button>
      </Group>
      <Paper withBorder p="md">
        <Text>
          Signed in as <strong>{user?.display_name}</strong> ({user?.email})
        </Text>
        <Text size="sm" c="dimmed">
          Role: {user?.is_admin ? 'Admin' : user?.read_only ? 'Read only' : 'Writer'}
        </Text>
      </Paper>
    </Stack>
  )
}
