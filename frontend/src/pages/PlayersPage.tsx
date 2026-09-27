import { useMemo, useState } from 'react'
import { Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listPlayers } from '../api/players'
import type { Player } from '../api/types'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'

export function PlayersPage() {
  const [search, setSearch] = useState('')
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)

  const { data: players, isLoading } = useQuery({ queryKey: ['players'], queryFn: listPlayers })

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
      <PlayerDetailDrawer player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
    </Stack>
  )
}
