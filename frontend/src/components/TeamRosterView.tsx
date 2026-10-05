import { useState } from 'react'
import { ActionIcon, Button, Group, Modal, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconArrowsExchange, IconLink, IconPencil, IconTrash } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getPlayerStats, listDivisionTeams } from '../api/divisions'
import {
  createPlayer,
  getMoveNotes,
  getPosition,
  getSeasonGrade,
  listPlayers,
  setPosition as apiSetPosition,
  setSeasonGrade as apiSetSeasonGrade,
} from '../api/players'
import { addRosterEntry, listRoster, movePlayer, removeRosterEntry, updateRosterEntry } from '../api/teams'
import { getDraftPool } from '../api/draft'
import type { RosterEntry } from '../api/types'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { CoachManager } from './CoachManager'
import { ReadOnlyNotice, Writable } from '../auth/access'

const POSITION_OPTIONS = ['', 'Forward', 'Defense', 'Forward or Defense', 'Goalie']

function PositionCell({ playerId, divisionId, teamId }: { playerId: number; divisionId: number; teamId: number }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['position', playerId, divisionId, teamId],
    queryFn: () => getPosition(playerId, divisionId, teamId),
  })
  const mutation = useMutation({
    mutationFn: (position: string) => apiSetPosition(playerId, { division_id: divisionId, team_id: teamId, position }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['position', playerId, divisionId, teamId] }),
  })
  // No position set on this team yet: show the one they registered with (e.g.
  // a registered goalie reads as Goalie right after the draft).
  const shown = data?.position || data?.registered || ''
  return (
    <Select
      size="xs"
      data={[...new Set([...POSITION_OPTIONS, shown])].map((p) => ({ value: p, label: p || '—' }))}
      value={shown}
      onChange={(v) => v != null && v !== shown && mutation.mutate(v)}
      allowDeselect={false}
    />
  )
}

function GradeCell({ playerId, divisionId, teamId }: { playerId: number; divisionId: number; teamId: number }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['season-grade', playerId, divisionId],
    queryFn: () => getSeasonGrade(playerId, divisionId),
  })
  const [value, setValue] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: (grade: string) => apiSetSeasonGrade(playerId, { division_id: divisionId, team_id: teamId, grade }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['season-grade', playerId, divisionId] })
      setValue(null)
    },
  })
  return (
    <TextInput
      size="xs"
      w={70}
      value={value ?? data?.grade ?? ''}
      onChange={(e) => setValue(e.currentTarget.value)}
      onBlur={() => value != null && value !== (data?.grade ?? '') && mutation.mutate(value)}
    />
  )
}

export function TeamRosterView({ teamId, divisionId }: { teamId: number; divisionId: number }) {
  const { user } = useAuth()
  const queryClient = useQueryClient()

  const [addNumber, setAddNumber] = useState('')
  const [addPlayerId, setAddPlayerId] = useState<string | null>(null)
  const [editEntry, setEditEntry] = useState<RosterEntry | null>(null)
  const [editNumber, setEditNumber] = useState('')
  const [editName, setEditName] = useState('')
  const [moveEntry, setMoveEntry] = useState<RosterEntry | null>(null)
  const [moveTeamId, setMoveTeamId] = useState<string | null>(null)
  const [moveNote, setMoveNote] = useState('')
  const [linkEntry, setLinkEntry] = useState<RosterEntry | null>(null)
  const [linkPlayerId, setLinkPlayerId] = useState<string | null>(null)
  const [newFirst, setNewFirst] = useState('')
  const [newLast, setNewLast] = useState('')

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId),
    enabled: !!divisionId,
  })
  const team = teams?.find((t) => t.id === teamId)

  const { data: roster, isLoading } = useQuery({
    queryKey: ['roster', teamId],
    queryFn: () => listRoster(teamId),
  })

  const { data: divisionStats } = useQuery({
    queryKey: ['stats', divisionId],
    queryFn: () => getPlayerStats(divisionId),
    enabled: !!divisionId,
  })
  const statsForTeam = (divisionStats ?? []).filter((s) => s.team_id === teamId)

  const { data: allPlayers } = useQuery({
    queryKey: ['players'],
    queryFn: () => listPlayers(),
    enabled: linkEntry != null,
  })
  // Only players registered in this division can be linked to its rosters.
  const linkablePlayers = (allPlayers ?? []).filter(
    (p) => p.name.trim().toLowerCase() !== 'sub' && p.division_ids.includes(divisionId)
  )

  // Who can be added: registered in this division and not on any of its teams
  // yet (the draft pool).
  const { data: pool } = useQuery({
    queryKey: ['draft-pool', divisionId],
    queryFn: () => getDraftPool(divisionId),
    enabled: !!divisionId,
  })
  // A jersey number left blank gets the next free placeholder, like the draft's.
  const nextPlaceholder = () => {
    const taken = new Set((roster ?? []).map((e) => e.number))
    let n = 1
    while (taken.has(`TBD${n}`)) n += 1
    return `TBD${n}`
  }

  const { data: moveNotes } = useQuery({
    queryKey: ['move-notes', moveEntry?.player_id, divisionId],
    queryFn: () => getMoveNotes(moveEntry!.player_id!, divisionId),
    enabled: !!user?.is_admin && moveEntry?.player_id != null,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })
  const invalidateRoster = () => queryClient.invalidateQueries({ queryKey: ['roster', teamId] })

  const addMutation = useMutation({
    mutationFn: () => {
      const player = (pool ?? []).find((p) => String(p.id) === addPlayerId)!
      return addRosterEntry(teamId, {
        number: addNumber.trim() || nextPlaceholder(),
        name: player.name,
        player_id: player.id,
      })
    },
    onSuccess: () => {
      invalidateRoster()
      for (const key of [['draft-pool', divisionId], ['division-players'], ['teams-overview', divisionId], ['division-requests']]) {
        queryClient.invalidateQueries({ queryKey: key })
      }
      setAddNumber('')
      setAddPlayerId(null)
    },
    onError,
  })

  // The number typed into the edit dialog already belongs to this teammate:
  // saving trades the two numbers.
  const swapWith =
    editEntry == null ? undefined : (roster ?? []).find((e) => e.id !== editEntry.id && e.number === editNumber.trim())

  const editMutation = useMutation({
    mutationFn: () =>
      updateRosterEntry(teamId, editEntry!.id, {
        number: editNumber.trim(),
        name: editName.trim(),
        swap_numbers: swapWith != null,
      }),
    onSuccess: () => {
      invalidateRoster()
      setEditEntry(null)
    },
    onError,
  })

  const removeMutation = useMutation({
    mutationFn: (entryId: number) => removeRosterEntry(teamId, entryId),
    onSuccess: invalidateRoster,
    onError,
  })

  const closeLink = () => {
    setLinkEntry(null)
    setLinkPlayerId(null)
    setNewFirst('')
    setNewLast('')
  }

  // Link an unlinked roster entry (e.g. a jersey number read off a scanned
  // sheet) to an existing player, or to a brand-new one created here.
  const linkMutation = useMutation({
    mutationFn: async (mode: 'existing' | 'new') => {
      let playerId = Number(linkPlayerId)
      if (mode === 'new') {
        const created = await createPlayer({
          first_name: newFirst.trim(),
          last_name: newLast.trim() || null,
          current_division_id: divisionId,
        })
        playerId = created.id
      }
      return updateRosterEntry(teamId, linkEntry!.id, { player_id: playerId })
    },
    onSuccess: () => {
      invalidateRoster()
      queryClient.invalidateQueries({ queryKey: ['players'] })
      queryClient.invalidateQueries({ queryKey: ['stats', divisionId] })
      closeLink()
    },
    onError,
  })

  const moveMutation = useMutation({
    mutationFn: () =>
      movePlayer(moveEntry!.player_id!, {
        division_id: divisionId,
        new_team_id: Number(moveTeamId),
        note: moveNote.trim() || undefined,
      }),
    onSuccess: () => {
      invalidateRoster()
      queryClient.invalidateQueries({ queryKey: ['move-notes'] })
      setMoveEntry(null)
      setMoveTeamId(null)
      setMoveNote('')
    },
    onError,
  })

  return (
    <Writable>
    <Stack>
      <ReadOnlyNotice />
      <Title order={4}>Roster</Title>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>#</Table.Th>
            <Table.Th>Name</Table.Th>
            <Table.Th>Position</Table.Th>
            <Table.Th>Grade</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(roster ?? []).map((entry) => (
            <Table.Tr key={entry.id}>
              <Table.Td>{entry.number}</Table.Td>
              <Table.Td>{entry.name}</Table.Td>
              <Table.Td>
                {entry.player_id ? (
                  <PositionCell playerId={entry.player_id} divisionId={divisionId} teamId={teamId} />
                ) : (
                  <Button
                    size="compact-xs"
                    variant="light"
                    leftSection={<IconLink size={14} />}
                    onClick={() => {
                      const [first, ...rest] = entry.name.split(' ')
                      setNewFirst(first ?? '')
                      setNewLast(rest.join(' '))
                      setLinkEntry(entry)
                    }}
                  >
                    Link player
                  </Button>
                )}
              </Table.Td>
              <Table.Td>
                {entry.player_id ? (
                  <GradeCell playerId={entry.player_id} divisionId={divisionId} teamId={teamId} />
                ) : (
                  '—'
                )}
              </Table.Td>
              <Table.Td>
                <Group gap={4}>
                  <ActionIcon
                    variant="subtle"
                    onClick={() => {
                      setEditEntry(entry)
                      setEditNumber(entry.number)
                      setEditName(entry.name)
                    }}
                  >
                    <IconPencil size={16} />
                  </ActionIcon>
                  {entry.player_id && (
                    <ActionIcon variant="subtle" onClick={() => setMoveEntry(entry)}>
                      <IconArrowsExchange size={16} />
                    </ActionIcon>
                  )}
                  <ActionIcon color="red" variant="subtle" onClick={() => removeMutation.mutate(entry.id)}>
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {!isLoading && roster?.length === 0 && (
        <Text c="dimmed" size="sm">
          No players on this roster yet.
        </Text>
      )}

      <Group align="flex-end">
        <Select
          label="Add a player"
          placeholder={(pool ?? []).length ? 'Search registered players…' : 'Everyone registered is on a team'}
          description="Players registered in this division who aren't on a team yet."
          data={(pool ?? []).map((p) => ({
            value: String(p.id),
            label: `${p.name}${p.position ? ` · ${p.position}` : ''}${p.birth_date ? ` · born ${p.birth_date}` : ''}`,
          }))}
          value={addPlayerId}
          onChange={setAddPlayerId}
          searchable
          clearable
          disabled={!(pool ?? []).length}
          flex={1}
          miw={260}
        />
        <TextInput
          label="Number"
          placeholder={nextPlaceholder()}
          value={addNumber}
          onChange={(e) => setAddNumber(e.currentTarget.value)}
          w={110}
        />
        <Button disabled={!addPlayerId} loading={addMutation.isPending} onClick={() => addMutation.mutate()}>
          Add to team
        </Button>
      </Group>

      <Title order={4} mt="md">
        Coaches
      </Title>
      <CoachManager teamId={teamId} />

      <Title order={4} mt="md">
        Player Stats
      </Title>
      {statsForTeam.length === 0 ? (
        <Text size="sm" c="dimmed">
          No stats recorded yet for this team.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>Name</Table.Th>
              <Table.Th>G</Table.Th>
              <Table.Th>A</Table.Th>
              <Table.Th>PTS</Table.Th>
              <Table.Th>PIM</Table.Th>
              <Table.Th>SO Made</Table.Th>
              <Table.Th>SO Missed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {statsForTeam.map((s, i) => (
              <Table.Tr key={i}>
                <Table.Td>{s.number}</Table.Td>
                <Table.Td>{s.name}</Table.Td>
                <Table.Td>{s.goals}</Table.Td>
                <Table.Td>{s.assists}</Table.Td>
                <Table.Td>{s.points}</Table.Td>
                <Table.Td>{s.penalties}</Table.Td>
                <Table.Td>{s.shootout_goals}</Table.Td>
                <Table.Td>{s.shootout_misses}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={editEntry != null} onClose={() => setEditEntry(null)} title="Edit roster entry">
        <Stack>
          <TextInput label="Number" value={editNumber} onChange={(e) => setEditNumber(e.currentTarget.value)} />
          <TextInput label="Name" value={editName} onChange={(e) => setEditName(e.currentTarget.value)} />
          {swapWith && (
            <Text size="sm" c="dimmed">
              #{swapWith.number} is {swapWith.name}&apos;s. Saving swaps the two numbers: {swapWith.name} gets #
              {editEntry?.number}.
            </Text>
          )}
          <Button loading={editMutation.isPending} onClick={() => editMutation.mutate()}>
            {swapWith ? 'Swap numbers' : 'Save'}
          </Button>
        </Stack>
      </Modal>

      <Modal opened={moveEntry != null} onClose={() => setMoveEntry(null)} title={`Move ${moveEntry?.name}`}>
        <Stack>
          <Select
            label="Team"
            data={(teams ?? []).filter((t) => t.id !== teamId).map((t) => ({ value: String(t.id), label: t.name }))}
            value={moveTeamId}
            onChange={setMoveTeamId}
          />
          {user?.is_admin && (
            <TextInput
              label="Reason (admin only — coaches won't see this)"
              value={moveNote}
              onChange={(e) => setMoveNote(e.currentTarget.value)}
            />
          )}
          <Button disabled={!moveTeamId} loading={moveMutation.isPending} onClick={() => moveMutation.mutate()}>
            Move
          </Button>
          {user?.is_admin && moveNotes && moveNotes.length > 0 && (
            <div>
              <Text size="xs" c="dimmed" mb={4}>
                Move history (admin only)
              </Text>
              {moveNotes.map((m) => (
                <Text key={m.id} size="sm">
                  {m.from_team ?? '?'} → {m.to_team ?? '?'}: {m.note || '(no reason given)'}
                </Text>
              ))}
            </div>
          )}
        </Stack>
      </Modal>

      <Modal opened={linkEntry != null} onClose={closeLink} title={`Link ${team?.name ?? ''} #${linkEntry?.number ?? ''}`}>
        <Stack>
          <Text size="sm" c="dimmed">
            &quot;{linkEntry?.name}&quot; isn&apos;t linked to a player profile yet. Pick the registered player it is, or
            create a new profile (registered in this division).
          </Text>
          <Group align="flex-end">
            <Select
              label="Player registered in this division"
              placeholder="Search by name"
              data={linkablePlayers.map((p) => ({ value: String(p.id), label: p.name }))}
              value={linkPlayerId}
              onChange={setLinkPlayerId}
              searchable
              flex={1}
            />
            <Button
              disabled={!linkPlayerId}
              loading={linkMutation.isPending && linkMutation.variables === 'existing'}
              onClick={() => linkMutation.mutate('existing')}
            >
              Link
            </Button>
          </Group>
          <Text size="xs" c="dimmed" ta="center">
            — or —
          </Text>
          <Group align="flex-end" grow>
            <TextInput label="First name" value={newFirst} onChange={(e) => setNewFirst(e.currentTarget.value)} />
            <TextInput label="Last name" value={newLast} onChange={(e) => setNewLast(e.currentTarget.value)} />
          </Group>
          <Button
            variant="light"
            disabled={!newFirst.trim()}
            loading={linkMutation.isPending && linkMutation.variables === 'new'}
            onClick={() => linkMutation.mutate('new')}
          >
            Create new player &amp; link
          </Button>
        </Stack>
      </Modal>
    </Stack>
    </Writable>
  )
}
