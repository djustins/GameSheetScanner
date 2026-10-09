import { useState } from 'react'
import { Alert, Button, Code, Group, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createToken, listTokens, revokeToken } from '../api/tokens'
import { ApiError } from '../api/client'

/** `embedded`: shown as a section of My Account rather than as its own page. */
export function ApiTokensPage({ embedded = false }: { embedded?: boolean }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [newToken, setNewToken] = useState<string | null>(null)

  const { data: tokens } = useQuery({ queryKey: ['tokens'], queryFn: listTokens })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const createMutation = useMutation({
    mutationFn: () => createToken(name.trim()),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] })
      setNewToken(created.token)
      setName('')
    },
    onError,
  })

  const revokeMutation = useMutation({
    mutationFn: (tokenId: number) => revokeToken(tokenId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tokens'] }),
    onError,
  })

  const activeTokens = (tokens ?? []).filter((t) => !t.revoked_at)
  const revokedTokens = (tokens ?? []).filter((t) => t.revoked_at)

  return (
    <Stack maw={700}>
      <Title order={embedded ? 4 : 2}>API Tokens</Title>
      <Text size="sm" c="dimmed">
        A long-lived alternative to your email/password for a script or integration — sent as{' '}
        <Code>Authorization: Bearer &lt;token&gt;</Code>.
      </Text>

      {newToken && (
        <Alert color="green" title="Save this token now" withCloseButton onClose={() => setNewToken(null)}>
          <Text size="sm" mb="xs">
            This is the only time it will be shown. Store it somewhere safe.
          </Text>
          <Code block>{newToken}</Code>
        </Alert>
      )}

      <Group align="flex-end">
        <TextInput label="Name" placeholder="e.g. My Laptop" value={name} onChange={(e) => setName(e.currentTarget.value)} flex={1} />
        <Button disabled={!name.trim()} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>
          Create Token
        </Button>
      </Group>

      <Title order={5} mt="md">
        Active Tokens
      </Title>
      {activeTokens.length === 0 ? (
        <Text size="sm" c="dimmed">
          No active tokens.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Created</Table.Th>
              <Table.Th>Last used</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {activeTokens.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td>{t.name}</Table.Td>
                <Table.Td>{t.created_at}</Table.Td>
                <Table.Td>{t.last_used_at ?? 'Never'}</Table.Td>
                <Table.Td>
                  <Button size="xs" variant="subtle" color="red" onClick={() => revokeMutation.mutate(t.id)}>
                    Revoke
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {revokedTokens.length > 0 && (
        <>
          <Title order={5} mt="md">
            Revoked
          </Title>
          <Stack gap={4}>
            {revokedTokens.map((t) => (
              <Text size="sm" c="dimmed" key={t.id}>
                {t.name} — revoked {t.revoked_at}
              </Text>
            ))}
          </Stack>
        </>
      )}
    </Stack>
  )
}
