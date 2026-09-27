import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ActionIcon,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconArrowsExchange, IconPencil, IconTrash } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getPlayerStats, listDivisionTeams } from '../api/divisions'
import { getPosition, setPosition as apiSetPosition, getSeasonGrade, setSeasonGrade as apiSetSeasonGrade } from '../api/players'
import {
  addRosterEntry,
  listRoster,
  movePlayer,
  removeRosterEntry,
  updateRosterEntry,
} from '../api/teams'
import type { RosterEntry } from '../api/types'
import { ApiError } from '../api/client'
import { CoachManager } from '../components/CoachManager'
import { useAuth } from '../auth/AuthContext'

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
  return (
    <Select
      size="xs"
      data={POSITION_OPTIONS.map((p) => ({ value: p, label: p || '—' }))}
      value={data?.position ?? ''}
      onChange={(v) => v != null && mutation.mutate(v)}
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

export function TeamRosterPage() {
  const { teamId: teamIdParam } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const teamId = Number(teamIdParam)
  const divisionId = Number(searchParams.get('division'))
  const queryClient = useQueryClient()

  const [addNumber, setAddNumber] = useState('')
  const [addName, setAddName] = useState('')
  const [editEntry, setEditEntry] = useState<RosterEntry | null>(null)
  const [editNumber, setEditNumber] = useState('')
  const [editName, setEditName] = useState('')
  const [moveEntry, setMoveEntry] = useState<RosterEntry | null>(null)
  const [moveTeamId, setMoveTeamId] = useState<string | null>(null)
  const [moveNote, setMoveNote] = useState('')

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

  const { data: teamStats } = useQuery({
    queryKey: ['team-stats', divisionId, team?.name],
    queryFn: () => getPlayerStats(divisionId),
    enabled: !!divisionId && !!team,
  })
  const statsForTeam = (teamStats ?? []).filter((s) => s.team === team?.name.toLowerCase())

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })
  const invalidateRoster = () => queryClient.invalidateQueries({ queryKey: ['roster', teamId] })

  const addMutation = useMutation({
    mutationFn: () => addRosterEntry(teamId, { number: addNumber.trim(), name: addName.trim() }),
    onSuccess: () => {
      invalidateRoster()
      setAddNumber('')
      setAddName('')
    },
    onError,
  })

  const editMutation = useMutation({
    mutationFn: () =>
      updateRosterEntry(teamId, editEntry!.id, { number: editNumber.trim(), name: editName.trim() }),
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

  const moveMutation = useMutation({
    mutationFn: () =>
      movePlayer(moveEntry!.player_id!, {
        division_id: divisionId,
        new_team_id: Number(moveTeamId),
        note: moveNote.trim() || undefined,
      }),
    onSuccess: () => {
      invalidateRoster()
      setMoveEntry(null)
      setMoveTeamId(null)
      setMoveNote('')
    },
    onError,
  })

  if (!divisionId) {
    return <Text c="red">Missing division context — go back to Divisions and open this team from there.</Text>
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{team?.name ?? `Team #${teamId}`}</Title>
        <Button variant="subtle" onClick={() => navigate(-1)}>
          Back
        </Button>
      </Group>

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
                  '—'
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

      <Group>
        <TextInput placeholder="Number" value={addNumber} onChange={(e) => setAddNumber(e.currentTarget.value)} w={100} />
        <TextInput placeholder="Name" value={addName} onChange={(e) => setAddName(e.currentTarget.value)} flex={1} />
        <Button
          disabled={!addNumber.trim() || !addName.trim()}
          loading={addMutation.isPending}
          onClick={() => addMutation.mutate()}
        >
          Add Player
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
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={editEntry != null} onClose={() => setEditEntry(null)} title="Edit roster entry">
        <Stack>
          <TextInput label="Number" value={editNumber} onChange={(e) => setEditNumber(e.currentTarget.value)} />
          <TextInput label="Name" value={editName} onChange={(e) => setEditName(e.currentTarget.value)} />
          <Button loading={editMutation.isPending} onClick={() => editMutation.mutate()}>
            Save
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
        </Stack>
      </Modal>
    </Stack>
  )
}
