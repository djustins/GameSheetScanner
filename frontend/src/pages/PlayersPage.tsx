import { useMemo, useState } from 'react'
import { Button, Group, Modal, MultiSelect, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { createPlayer, listPlayers, restorePlayer, type PlayerFields } from '../api/players'
import { getSeasonGrades, listDivisions } from '../api/divisions'
import type { Player } from '../api/types'
import { ApiError } from '../api/client'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'
import { useAuth } from '../auth/AuthContext'
import { useAccess } from '../auth/access'
import { divisionSeasonLabel } from '../utils/format'

const EMPTY_NEW_PLAYER = {
  first_name: '',
  last_name: '',
  nickname: '',
  birth_date: '',
  contact_first_name: '',
  contact_last_name: '',
  contact_phone: '',
  contact_email: '',
}

export function PlayersPage() {
  const { user } = useAuth()
  const { readOnly, hideContactDetails } = useAccess()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [divisionFilter, setDivisionFilter] = useState<string | null>(null)
  const [evalFilter, setEvalFilter] = useState<string[]>([])
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [newPlayer, setNewPlayer] = useState(EMPTY_NEW_PLAYER)
  const [newDivisionId, setNewDivisionId] = useState<string | null>(null)

  const { data: players, isLoading } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })
  const { data: allPlayers } = useQuery({ queryKey: ['players', 'all'], queryFn: () => listPlayers(true) })
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const deletedPlayers = (allPlayers ?? []).filter((p) => p.deleted_at)
  const divisionOptions = (divisions ?? []).map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))
  const divisionLabelById = new Map(divisionOptions.map((o) => [Number(o.value), o.label]))

  // A division's season grades are keyed by exactly the players evaluated
  // in it (both come from its evaluations), so they double as the
  // "has an evaluation for" set.
  const evalQueries = useQueries({
    queries: evalFilter.map((id) => ({
      queryKey: ['season-grades', Number(id)],
      queryFn: () => getSeasonGrades(Number(id)),
    })),
  })

  const restoreMutation = useMutation({
    mutationFn: restorePlayer,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
    },
  })

  const createMutation = useMutation({
    mutationFn: () => {
      const trimmed = (v: string) => v.trim() || null
      const fields: PlayerFields = {
        first_name: newPlayer.first_name.trim(),
        last_name: trimmed(newPlayer.last_name),
        nickname: trimmed(newPlayer.nickname),
        birth_date: trimmed(newPlayer.birth_date),
        current_division_id: newDivisionId ? Number(newDivisionId) : null,
        contact_first_name: trimmed(newPlayer.contact_first_name),
        contact_last_name: trimmed(newPlayer.contact_last_name),
      }
      if (!hideContactDetails) {
        fields.contact_phone = trimmed(newPlayer.contact_phone)
        fields.contact_email = trimmed(newPlayer.contact_email)
      }
      return createPlayer(fields)
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
      queryClient.invalidateQueries({ queryKey: ['parents'] })
      setCreateOpen(false)
      setNewPlayer(EMPTY_NEW_PLAYER)
      setNewDivisionId(null)
      setSelectedPlayer(created)
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to create player.' })
    },
  })

  // "Sub" placeholder players (one per team, from linking a roster's generic
  // "Sub" jersey row) aren't a real individual -- same exclusion as Streamlit.
  const realPlayers = useMemo(() => (players ?? []).filter((p) => p.name.trim().toLowerCase() !== 'sub'), [players])

  const evalSets = evalQueries.map((q) => (q.data ? new Set(Object.keys(q.data).map(Number)) : null))
  const evalLoading = evalSets.some((s) => s == null)

  const filtered = realPlayers.filter((p) => {
    const q = search.trim().toLowerCase()
    if (q && !p.name.toLowerCase().includes(q) && !(p.nickname ?? '').toLowerCase().includes(q)) return false
    if (divisionFilter && p.current_division_id !== Number(divisionFilter)) return false
    // Intersection, not union: evaluated in every selected division.
    if (!evalLoading && !evalSets.every((s) => s!.has(p.id))) return false
    return true
  })

  const field = (key: keyof typeof EMPTY_NEW_PLAYER, label: string, placeholder?: string) => (
    <TextInput
      label={label}
      placeholder={placeholder}
      value={newPlayer[key]}
      onChange={(e) => {
        const value = e.currentTarget.value
        setNewPlayer((p) => ({ ...p, [key]: value }))
      }}
      required={key === 'first_name'}
    />
  )

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>All Players</Title>
        {!readOnly && <Button onClick={() => setCreateOpen(true)}>New Player</Button>}
      </Group>
      <Group align="flex-end">
        <TextInput
          label="Search by name"
          placeholder="Name or nickname"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          w={260}
        />
        <Select
          label="Current division"
          placeholder="All divisions"
          data={divisionOptions}
          value={divisionFilter}
          onChange={setDivisionFilter}
          clearable
          searchable
          w={300}
        />
        <MultiSelect
          label="Has an evaluation for"
          placeholder={evalFilter.length ? undefined : 'Any'}
          description="Only players rated in every division picked"
          data={divisionOptions}
          value={evalFilter}
          onChange={setEvalFilter}
          clearable
          searchable
          w={320}
        />
      </Group>
      <Text size="sm" c="dimmed">
        Showing {filtered.length} of {realPlayers.length} players.
      </Text>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Born</Table.Th>
            <Table.Th>Current division</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {filtered.map((p) => (
            <Table.Tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedPlayer(p)}>
              <Table.Td>
                {p.name}
                {p.nickname ? ` "${p.nickname}"` : ''}
              </Table.Td>
              <Table.Td>{p.birth_date ?? '—'}</Table.Td>
              <Table.Td>
                {p.current_division_id != null ? (divisionLabelById.get(p.current_division_id) ?? '—') : '—'}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {!isLoading && filtered.length === 0 && (
        <Text c="dimmed" size="sm">
          {realPlayers.length === 0 ? 'No players yet — add one above.' : 'No players match these filters.'}
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

      <PlayerDetailDrawer
        player={selectedPlayer}
        onClose={() => setSelectedPlayer(null)}
        navList={filtered}
        onNavigate={setSelectedPlayer}
      />

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="New Player" size="lg">
        <Stack>
          <Group grow>
            {field('first_name', 'First name')}
            {field('last_name', 'Last name')}
            {field('nickname', 'Nickname')}
          </Group>
          <Group grow>
            {field('birth_date', 'Birth date', 'YYYY-MM-DD')}
            <Select
              label="Current division"
              placeholder="(none)"
              data={divisionOptions}
              value={newDivisionId}
              onChange={setNewDivisionId}
              clearable
              searchable
            />
          </Group>
          <Group grow>
            {field('contact_first_name', 'Contact first name')}
            {field('contact_last_name', 'Contact last name')}
          </Group>
          {hideContactDetails ? (
            <Text size="xs" c="dimmed">
              🔒 Phone/email are hidden for your role.
            </Text>
          ) : (
            <Group grow>
              {field('contact_phone', 'Contact phone')}
              {field('contact_email', 'Contact email')}
            </Group>
          )}
          <Button
            disabled={!newPlayer.first_name.trim()}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            Add player
          </Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
