import { useMemo, useState } from 'react'
import { Button, Group, Modal, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createPlayer, listPlayers, restorePlayer } from '../api/players'
import type { Player } from '../api/types'
import { ApiError } from '../api/client'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'
import { useAuth } from '../auth/AuthContext'

export function PlayersPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [newFirst, setNewFirst] = useState('')
  const [newLast, setNewLast] = useState('')

  const { data: players, isLoading } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })
  const { data: allPlayers } = useQuery({ queryKey: ['players', 'all'], queryFn: () => listPlayers(true) })
  const deletedPlayers = (allPlayers ?? []).filter((p) => p.deleted_at)

  const restoreMutation = useMutation({
    mutationFn: restorePlayer,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
    },
  })

  const createMutation = useMutation({
    mutationFn: () => createPlayer({ first_name: newFirst.trim(), last_name: newLast.trim() || null }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
      setCreateOpen(false)
      setNewFirst('')
      setNewLast('')
      setSelectedPlayer(created)
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to create player.' })
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
      <Group justify="space-between">
        <Title order={2}>All Players</Title>
        <Button onClick={() => setCreateOpen(true)}>New Player</Button>
      </Group>
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

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="New Player">
        <Stack>
          <TextInput label="First name" value={newFirst} onChange={(e) => setNewFirst(e.currentTarget.value)} required />
          <TextInput label="Last name" value={newLast} onChange={(e) => setNewLast(e.currentTarget.value)} />
          <Button disabled={!newFirst.trim()} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>
            Create
          </Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
