import { useMemo, useState } from 'react'
import { Button, Group, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listPlayers, restorePlayer } from '../api/players'
import type { Player } from '../api/types'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'
import { useAuth } from '../auth/AuthContext'

export function PlayersPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)

  const { data: players, isLoading } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })
  const { data: allPlayers } = useQuery({ queryKey: ['players', 'all'], queryFn: () => listPlayers(true) })
  const deletedPlayers = (allPlayers ?? []).filter((p) => p.deleted_at)

  const restoreMutation = useMutation({
    mutationFn: restorePlayer,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
    },
  })

  const filtered = useMemo(() => {
    if (!players) return []
    const q = search.trim().toLowerCase()
    if (!q) return players
    return players.filter((p) => p.name.toLowerCase().includes(q))
  }, [players, search])

  return (
    <Stack>
      <Title order={2}>All Players</Title>
      <Text size="sm" c="dimmed">
        {players?.length ?? 0} players registered
      </Text>
      <TextInput
        placeholder="Search by name"
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        w={320}
      />
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Born</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {filtered.map((p) => (
            <Table.Tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedPlayer(p)}>
              <Table.Td>{p.name}</Table.Td>
              <Table.Td>{p.birth_date ?? '—'}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {!isLoading && filtered.length === 0 && (
        <Text c="dimmed" size="sm">
          No players found.
        </Text>
      )}

      {user?.is_admin && deletedPlayers.length > 0 && (
        <>
          <Title order={5} mt="md">
            Deleted Players ({deletedPlayers.length})
          </Title>
          <Stack gap="xs">
            {deletedPlayers.map((p) => (
              <Group key={p.id} justify="space-between">
                <Text size="sm">{p.name}</Text>
                <Button size="xs" variant="subtle" onClick={() => restoreMutation.mutate(p.id)}>
                  Restore
                </Button>
              </Group>
            ))}
          </Stack>
        </>
      )}

      <PlayerDetailDrawer player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
    </Stack>
  )
}
