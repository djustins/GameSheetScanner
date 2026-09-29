import { useState } from 'react'
import { Badge, Button, Drawer, Group, Loader, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addEvaluation,
  addPlayerRequest,
  deleteEvaluation,
  deletePlayer,
  getPlayerHistory,
  getSiblings,
  listEvaluations,
  listPlayerRequests,
  listPlayers,
  removePlayerRequest,
  updatePlayer,
} from '../api/players'
import { linkSiblings, listParents, setPlayerParent } from '../api/parents'
import { listDivisions } from '../api/divisions'
import type { Player } from '../api/types'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ReadOnlyNotice, useAccess, Writable } from '../auth/access'
import { coachLabel, divisionLabel, divisionSeasonLabel } from '../utils/format'

interface Props {
  player: Player | null
  onClose: () => void
  // The list the player was opened from, for Previous/Next (optional).
  navList?: Player[]
  onNavigate?: (player: Player) => void
}

export function PlayerDetailDrawer({ player, onClose, navList, onNavigate }: Props) {
  const { user } = useAuth()
  const { hideContactDetails } = useAccess()
  const queryClient = useQueryClient()
  const [parentPickId, setParentPickId] = useState<string | null>(null)
  const [siblingPickId, setSiblingPickId] = useState<string | null>(null)
  const [requestPickId, setRequestPickId] = useState<string | null>(null)
  const [requestNote, setRequestNote] = useState('')
  const [evalDivisionId, setEvalDivisionId] = useState<string | null>(null)
  const [evalGrade, setEvalGrade] = useState('')

  const { data: history, isLoading } = useQuery({
    queryKey: ['player-history', player?.id],
    queryFn: () => getPlayerHistory(player!.id),
    enabled: player != null,
  })

  const { data: siblings } = useQuery({
    queryKey: ['siblings', player?.id],
    queryFn: () => getSiblings(player!.id),
    enabled: player != null,
  })

  const { data: requests } = useQuery({
    queryKey: ['player-requests', player?.id],
    queryFn: () => listPlayerRequests(player!.id),
    enabled: player != null,
  })

  const { data: parents } = useQuery({ queryKey: ['parents'], queryFn: listParents })
  const { data: allPlayers } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data: evaluations } = useQuery({
    queryKey: ['evaluations', player?.id],
    queryFn: () => listEvaluations(player!.id),
    enabled: player != null,
  })

  // `player` is the caller's snapshot; the refreshed list reflects saves made
  // here (needed for controlled fields like the division picker).
  const livePlayer = (allPlayers ?? []).find((p) => p.id === player?.id) ?? player
  const currentDivisionId = livePlayer?.current_division_id ?? null

  // Derived, like Streamlit's "Current Team Number": this player's roster
  // row(s) in their current division.
  const currentTeamEntries = (history ?? []).filter(
    (h) => currentDivisionId != null && h.division_id === currentDivisionId
  )
  const currentTeamDisplay =
    currentDivisionId == null
      ? '—'
      : currentTeamEntries.length
        ? currentTeamEntries.map((h) => `#${h.number} (${h.team_name})`).join(', ')
        : 'Not on a roster yet'

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const invalidatePlayerLists = () => {
    queryClient.invalidateQueries({ queryKey: ['players'] })
    queryClient.invalidateQueries({ queryKey: ['division-players'] })
  }

  const saveMutation = useMutation({
    mutationFn: (fields: Partial<Player>) => updatePlayer(player!.id, fields),
    onSuccess: () => {
      invalidatePlayerLists()
      notifications.show({ message: 'Saved.' })
    },
    onError,
  })

  const setParentMutation = useMutation({
    mutationFn: (parentId: number | null) => setPlayerParent(player!.id, parentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['siblings', player?.id] })
      setParentPickId(null)
    },
    onError,
  })

  const linkSiblingMutation = useMutation({
    mutationFn: () => linkSiblings(player!.id, Number(siblingPickId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['siblings', player?.id] })
      setSiblingPickId(null)
    },
    onError,
  })

  // Either side of a request shows it, so both players' cached lists are stale.
  const invalidateRequests = () => queryClient.invalidateQueries({ queryKey: ['player-requests'] })

  const addRequestMutation = useMutation({
    mutationFn: () => addPlayerRequest(player!.id, Number(requestPickId), requestNote.trim()),
    onSuccess: () => {
      invalidateRequests()
      setRequestPickId(null)
      setRequestNote('')
    },
    onError,
  })

  const removeRequestMutation = useMutation({
    mutationFn: (requestId: number) => removePlayerRequest(player!.id, requestId),
    onSuccess: invalidateRequests,
    onError,
  })

  const addEvalMutation = useMutation({
    mutationFn: () => addEvaluation(player!.id, { division_id: Number(evalDivisionId), grade: evalGrade.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluations', player?.id] })
      setEvalDivisionId(null)
      setEvalGrade('')
    },
    onError,
  })

  const deleteEvalMutation = useMutation({
    mutationFn: (evaluationId: number) => deleteEvaluation(player!.id, evaluationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['evaluations', player?.id] }),
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deletePlayer(player!.id),
    onSuccess: () => {
      invalidatePlayerLists()
      onClose()
    },
    onError,
  })

  return (
    <Drawer opened={player != null} onClose={onClose} title={player?.name} position="right" size="xl">
      {player && navList && onNavigate && (() => {
        const idx = navList.findIndex((p) => p.id === player.id)
        return idx >= 0 ? (
          <Group justify="space-between" mb="sm">
            <Button size="xs" variant="subtle" disabled={idx === 0} onClick={() => onNavigate(navList[idx - 1])}>
              ◀ Previous
            </Button>
            <Text size="xs" c="dimmed">
              {idx + 1} of {navList.length}
            </Text>
            <Button
              size="xs"
              variant="subtle"
              disabled={idx === navList.length - 1}
              onClick={() => onNavigate(navList[idx + 1])}
            >
              Next ▶
            </Button>
          </Group>
        ) : null
      })()}
      {player && (
        // Keyed so uncontrolled (defaultValue) inputs reset on Previous/Next.
        <Writable key={player.id}>
        <Stack>
          <ReadOnlyNotice />
          <div>
            <Title order={5} mb="xs">
              Profile
            </Title>
            <Group grow>
              <TextInput
                label="First name"
                defaultValue={player.first_name}
                onBlur={(e) => e.currentTarget.value !== player.first_name && saveMutation.mutate({ first_name: e.currentTarget.value })}
              />
              <TextInput
                label="Last name"
                defaultValue={player.last_name ?? ''}
                onBlur={(e) => e.currentTarget.value !== (player.last_name ?? '') && saveMutation.mutate({ last_name: e.currentTarget.value })}
              />
              <TextInput
                label="Nickname"
                defaultValue={player.nickname ?? ''}
                onBlur={(e) => e.currentTarget.value !== (player.nickname ?? '') && saveMutation.mutate({ nickname: e.currentTarget.value })}
              />
            </Group>
            <Group grow mt="xs">
              <TextInput
                label="Birth date"
                placeholder="YYYY-MM-DD"
                defaultValue={player.birth_date ?? ''}
                onBlur={(e) => e.currentTarget.value !== (player.birth_date ?? '') && saveMutation.mutate({ birth_date: e.currentTarget.value })}
              />
              {!hideContactDetails && (
                <>
                  <TextInput
                    label="Contact phone"
                    defaultValue={player.contact_phone ?? ''}
                    onBlur={(e) => e.currentTarget.value !== (player.contact_phone ?? '') && saveMutation.mutate({ contact_phone: e.currentTarget.value })}
                  />
                  <TextInput
                    label="Contact email"
                    defaultValue={player.contact_email ?? ''}
                    onBlur={(e) => e.currentTarget.value !== (player.contact_email ?? '') && saveMutation.mutate({ contact_email: e.currentTarget.value })}
                  />
                </>
              )}
            </Group>
            {hideContactDetails && (
              <Text size="xs" c="dimmed" mt={4}>
                🔒 Phone/email are hidden for your role.
              </Text>
            )}
            <Group grow mt="xs">
              <Select
                label="Current division"
                placeholder="(none)"
                data={(divisions ?? []).map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))}
                value={currentDivisionId != null ? String(currentDivisionId) : null}
                onChange={(v) => saveMutation.mutate({ current_division_id: v ? Number(v) : null })}
                clearable
                searchable
              />
              <TextInput
                label="Current team number"
                value={currentTeamDisplay}
                readOnly
                description="From Team Rosters — link this player to a roster row there to set it."
              />
            </Group>
            <Group grow mt="xs">
              <TextInput
                label="Contact first name"
                defaultValue={player.contact_first_name ?? ''}
                onBlur={(e) => e.currentTarget.value !== (player.contact_first_name ?? '') && saveMutation.mutate({ contact_first_name: e.currentTarget.value })}
              />
              <TextInput
                label="Contact last name"
                defaultValue={player.contact_last_name ?? ''}
                onBlur={(e) => e.currentTarget.value !== (player.contact_last_name ?? '') && saveMutation.mutate({ contact_last_name: e.currentTarget.value })}
              />
            </Group>
            <TextInput
              mt="xs"
              label="USA Ball Hockey ID"
              defaultValue={player.usa_ball_hockey_id ?? ''}
              onBlur={(e) => e.currentTarget.value !== (player.usa_ball_hockey_id ?? '') && saveMutation.mutate({ usa_ball_hockey_id: e.currentTarget.value })}
            />
          </div>

          <div>
            <Title order={5} mb="xs">
              Season History
            </Title>
            {isLoading && <Loader size="sm" />}
            {!isLoading && (!history || history.length === 0) && (
              <Text size="sm" c="dimmed">
                No season history on file yet.
              </Text>
            )}
            {!isLoading && history && history.length > 0 && (
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Season</Table.Th>
                    <Table.Th>Division</Table.Th>
                    <Table.Th>Team</Table.Th>
                    <Table.Th>Grade</Table.Th>
                    <Table.Th>Position</Table.Th>
                    <Table.Th>Coach</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {history.map((h, i) => (
                    <Table.Tr key={i}>
                      <Table.Td>
                        {h.year} {h.season}
                      </Table.Td>
                      <Table.Td>{divisionLabel(h.age_group, h.category)}</Table.Td>
                      <Table.Td>{h.team_name}</Table.Td>
                      <Table.Td>{h.grade ? <Badge variant="light">{h.grade}</Badge> : '—'}</Table.Td>
                      <Table.Td>{h.position ?? '—'}</Table.Td>
                      <Table.Td>{h.coaches.length ? h.coaches.map(coachLabel).join(', ') : '—'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </div>

          <div>
            <Title order={5} mb="xs">
              Evaluations
            </Title>
            {!evaluations || evaluations.length === 0 ? (
              <Text size="sm" c="dimmed" mb="xs">
                No evaluations on file yet.
              </Text>
            ) : (
              <Stack gap={4} mb="xs">
                {evaluations.map((e) => (
                  <Group key={e.id} justify="space-between">
                    <Group gap="xs">
                      <Text size="sm">
                        {e.year} {e.season}
                        {e.team_name ? ` — ${e.team_name}` : ''}:
                      </Text>
                      <Badge variant="light">{e.grade}</Badge>
                    </Group>
                    <Button size="compact-xs" variant="subtle" color="red" onClick={() => deleteEvalMutation.mutate(e.id)}>
                      Delete
                    </Button>
                  </Group>
                ))}
              </Stack>
            )}
            <Group>
              <Select
                placeholder="Division"
                data={(divisions ?? []).map((d) => ({ value: String(d.id), label: divisionLabel(d.age_group, d.category) + ` (${d.year} ${d.season})` }))}
                value={evalDivisionId}
                onChange={setEvalDivisionId}
                searchable
                flex={2}
              />
              <TextInput placeholder="Grade" value={evalGrade} onChange={(e) => setEvalGrade(e.currentTarget.value)} flex={1} />
              <Button size="xs" disabled={!evalDivisionId || !evalGrade.trim()} onClick={() => addEvalMutation.mutate()}>
                Add
              </Button>
            </Group>
          </div>

          <div>
            <Title order={5} mb="xs">
              Parent / Siblings
            </Title>
            {siblings && siblings.length > 0 ? (
              <Text size="sm" mb="xs">
                Siblings: {siblings.map((s) => s.name).join(', ')}
              </Text>
            ) : (
              <Text size="sm" c="dimmed" mb="xs">
                {player.parent_id ? 'No siblings on file.' : 'No parent linked yet.'}
              </Text>
            )}
            <Group>
              <Select
                placeholder="Link to a parent/guardian"
                data={(parents ?? []).map((p) => ({ value: String(p.id), label: p.name }))}
                value={parentPickId}
                onChange={setParentPickId}
                searchable
                flex={1}
              />
              <Button size="xs" disabled={!parentPickId} onClick={() => setParentMutation.mutate(Number(parentPickId))}>
                Link
              </Button>
              {player.parent_id && (
                <Button size="xs" variant="subtle" color="red" onClick={() => setParentMutation.mutate(null)}>
                  Unlink
                </Button>
              )}
            </Group>
            <Group mt="xs">
              <Select
                placeholder="Link another player as a sibling"
                data={(allPlayers ?? [])
                  .filter((p) => p.id !== player.id)
                  .map((p) => ({ value: String(p.id), label: p.name }))}
                value={siblingPickId}
                onChange={setSiblingPickId}
                searchable
                flex={1}
              />
              <Button
                size="xs"
                disabled={!siblingPickId}
                loading={linkSiblingMutation.isPending}
                onClick={() => linkSiblingMutation.mutate()}
              >
                Link as Sibling
              </Button>
            </Group>
          </div>

          <div>
            <Title order={5} mb={4}>
              Play-with Requests
            </Title>
            <Text size="xs" c="dimmed" mb="xs">
              A non-family ask to play with a specific player. Auto Draft honors it when it can without
              unbalancing teams — unlike siblings, which are always placed together.
            </Text>
            {!requests || requests.length === 0 ? (
              <Text size="sm" c="dimmed" mb="xs">
                No play-with requests on file.
              </Text>
            ) : (
              <Stack gap={4} mb="xs">
                {requests.map((r) => (
                  <Group key={r.id} justify="space-between">
                    <Text size="sm">
                      {r.direction === 'made' ? `Requested ${r.name}` : `Requested by ${r.name}`}
                      {r.note ? ` — ${r.note}` : ''}
                    </Text>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      onClick={() => removeRequestMutation.mutate(r.id)}
                    >
                      Remove
                    </Button>
                  </Group>
                ))}
              </Stack>
            )}
            <Group>
              <Select
                placeholder="Request to play with…"
                data={(allPlayers ?? [])
                  .filter((p) => p.id !== player.id)
                  .map((p) => ({ value: String(p.id), label: p.name }))}
                value={requestPickId}
                onChange={setRequestPickId}
                searchable
                flex={2}
              />
              <TextInput
                placeholder="Note (optional)"
                value={requestNote}
                onChange={(e) => setRequestNote(e.currentTarget.value)}
                flex={1}
              />
              <Button
                size="xs"
                disabled={!requestPickId}
                loading={addRequestMutation.isPending}
                onClick={() => addRequestMutation.mutate()}
              >
                Add
              </Button>
            </Group>
          </div>

          {user?.is_admin && (
            <Button
              color="red"
              variant="outline"
              onClick={() => confirm(`Delete ${player.name}? This can be undone from the recycle bin.`) && deleteMutation.mutate()}
            >
              Delete player
            </Button>
          )}
        </Stack>
        </Writable>
      )}
    </Drawer>
  )
}
