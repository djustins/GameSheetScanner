import { useMemo, useState } from 'react'
import {
  Alert,
  Badge,
  Box,
  Button,
  Grid,
  Group,
  Paper,
  ScrollArea,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery } from '@tanstack/react-query'
import { deleteDraft, getDraftPool, listDivisionRequests, listPicks, submitPick, undoLastPick } from '../api/draft'
import { getTeamsOverview } from '../api/divisions'
import { listTeamCoaches } from '../api/teams'
import { ApiError } from '../api/client'
import type { Draft, DraftOrderEntry, DraftPick, DraftPoolPlayer, Team } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useAccess } from '../auth/access'

// Other coaches are picking at the same time, so everything here re-reads on this beat.
const LIVE_MS = 5000

const GRADE_COLORS: Record<string, string> = { A: 'green', B: 'blue', C: 'yellow', D: 'orange' }

function GradeBadge({ grade }: { grade: string | null }) {
  if (!grade) {
    return (
      <Text span size="xs" c="dimmed">
        —
      </Text>
    )
  }
  return (
    <Badge size="sm" variant="light" color={GRADE_COLORS[grade[0]?.toUpperCase()] ?? 'gray'}>
      {grade}
    </Badge>
  )
}

// Best grade first, a move-up grade (A*) after the plain one, ungraded last.
function gradeRank(grade: string | null): number {
  if (!grade) return 99
  const tier = 'ABCD'.indexOf(grade[0].toUpperCase())
  return (tier === -1 ? 9 : tier) * 2 + (grade.includes('*') ? 1 : 0)
}

function shortPosition(position: string | null): string {
  const p = (position ?? '').toLowerCase()
  if (!p) return '—'
  if (p.includes('goal') || p === 'g') return 'G'
  const forward = p.includes('forward')
  const defense = p.includes('defen')
  return forward && defense ? 'F/D' : forward ? 'F' : defense ? 'D' : (position ?? '—')
}

function age(birthDate: string | null): number | null {
  if (!birthDate) return null
  const born = new Date(birthDate.length === 10 ? `${birthDate}T12:00:00` : birthDate)
  if (Number.isNaN(born.getTime())) return null
  return (Date.now() - born.getTime()) / (365.25 * 24 * 3600 * 1000)
}

const lastFirst = (p: { first_name: string; last_name: string | null }) =>
  p.last_name ? `${p.last_name}, ${p.first_name}` : p.first_name

// Whose pick a given overall pick number is: odd rounds run the order, even rounds run it back.
function slotForPick(order: DraftOrderEntry[], pickNumber: number): DraftOrderEntry {
  const n = order.length
  const round = Math.ceil(pickNumber / n)
  const pos = (pickNumber - 1) % n
  return order[round % 2 === 0 ? n - 1 - pos : pos]
}

type PoolSort = 'grade' | 'name' | 'age'

interface Props {
  draft: Draft
  divisionId: number
  teams: Team[]
  // After a pick, undo or delete: refresh everything the draft touches.
  onChanged: () => void
}

/** The live draft, laid out like a fantasy draft room: who's on the clock, the
 * round-by-team board, the player pool to pick from, each team's haul so far
 * and the pick-by-pick history. */
export function DraftRoom({ draft, divisionId, teams, onChanged }: Props) {
  const { user } = useAuth()
  const { readOnly } = useAccess()
  const [search, setSearch] = useState('')
  const [position, setPosition] = useState<string | null>(null)
  const [sort, setSort] = useState<PoolSort>('grade')
  const [teamId, setTeamId] = useState<number | null>(null)

  const live = draft.status === 'in_progress'
  const refetchInterval = live ? LIVE_MS : false

  const { data: pool } = useQuery({
    queryKey: ['draft-pool', divisionId],
    queryFn: () => getDraftPool(divisionId),
    refetchInterval,
  })
  const { data: picks } = useQuery({
    queryKey: ['draft-picks', draft.id],
    queryFn: () => listPicks(draft.id),
    refetchInterval,
  })
  const { data: overview } = useQuery({
    queryKey: ['teams-overview', divisionId],
    queryFn: () => getTeamsOverview(divisionId),
    refetchInterval,
  })
  const { data: requests } = useQuery({
    queryKey: ['division-requests', divisionId],
    queryFn: () => listDivisionRequests(divisionId),
  })
  const { data: currentTeamCoaches } = useQuery({
    queryKey: ['team-coaches', draft.current_team_id],
    queryFn: () => listTeamCoaches(draft.current_team_id!),
    enabled: !!draft.current_team_id,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const pickMutation = useMutation({
    mutationFn: (player: DraftPoolPlayer) => submitPick(draft.id, player.id),
    onSuccess: (_, player) => {
      notifications.show({ color: 'green', message: `${draft.current_team_name} select ${player.name}.` })
      onChanged()
    },
    onError,
  })
  const undoMutation = useMutation({ mutationFn: () => undoLastPick(draft.id), onSuccess: onChanged, onError })
  const deleteMutation = useMutation({ mutationFn: () => deleteDraft(draft.id), onSuccess: onChanged, onError })

  const canPick =
    !readOnly &&
    live &&
    !!draft.current_team_id &&
    (user?.is_admin || (user?.coach_id != null && (currentTeamCoaches ?? []).some((c) => c.id === user.coach_id)))

  const order = draft.order
  const n = order.length
  const allPicks = picks ?? []
  const totalPicks = allPicks.length + (pool?.length ?? 0)
  const rounds = Math.max(1, Math.ceil(totalPicks / n), draft.round ?? 1)
  const pickByNumber = new Map(allPicks.map((p) => [p.pick_number, p]))
  const colorOf = (id: number) => teams.find((t) => t.id === id)?.color ?? null
  const upNext = live
    ? [1, 2, 3]
        .map((ahead) => draft.current_pick_number + ahead)
        .filter((num) => num <= totalPicks)
        .map((num) => slotForPick(order, num).team_name)
    : []

  // What each pool player has asked for, and where that teammate already is.
  const requestNotes = useMemo(() => {
    const notes = new Map<number, string[]>()
    const add = (id: number, text: string) => notes.set(id, [...(notes.get(id) ?? []), text])
    for (const r of requests ?? []) {
      const label = r.avoid ? 'Not with' : r.hard ? 'Must be with' : 'Wants'
      add(r.player_id, `${label} ${r.other_name}${r.other_team ? ` (${r.other_team})` : ''}`)
      add(r.other_player_id, `${label} ${r.name}${r.team ? ` (${r.team})` : ''}`)
    }
    return notes
  }, [requests])

  const positions = [...new Set((pool ?? []).map((p) => p.position).filter((p): p is string => !!p))].sort()
  const shownPool = (pool ?? [])
    .filter((p) => !position || p.position === position)
    .filter((p) => {
      const q = search.trim().toLowerCase()
      return !q || p.name.toLowerCase().includes(q) || (p.nickname ?? '').toLowerCase().includes(q)
    })
    .sort((a, b) => {
      const byName = lastFirst(a).localeCompare(lastFirst(b))
      if (sort === 'grade') return gradeRank(a.grade) - gradeRank(b.grade) || byName
      // Oldest first; unknown birth dates last.
      if (sort === 'age') return (age(b.birth_date) ?? -1) - (age(a.birth_date) ?? -1) || byName
      return byName
    })

  const shownTeamId = teamId ?? draft.current_team_id ?? order[0]?.team_id
  const shownTeamPicks = allPicks.filter((p) => p.team_id === shownTeamId)
  const overviewOf = (id: number) => (overview ?? []).find((o) => o.team_id === id)

  return (
    <Stack ta="left">
      {/* On the clock */}
      <Paper p="md" radius="md" bg="var(--app-panel-bg)" style={{ borderLeft: '6px solid var(--mantine-color-gold-4)' }}>
        {live ? (
          <Group justify="space-between" align="center">
            <div>
              <Text size="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: 1 }}>
                On the clock · Round {draft.round} · Pick {draft.current_pick_number} of {totalPicks}
              </Text>
              <Group gap="xs" align="center">
                <TeamSwatch color={colorOf(draft.current_team_id!)} size={18} />
                <Title order={2}>{draft.current_team_name}</Title>
              </Group>
              {(currentTeamCoaches ?? []).length > 0 && (
                <Text size="sm" c="dimmed">
                  {currentTeamCoaches!.map((c) => c.name).join(', ')}
                </Text>
              )}
            </div>
            <div style={{ textAlign: 'right' }}>
              <Text size="sm">
                <b>{pool?.length ?? 0}</b> left in the pool
              </Text>
              {upNext.length > 0 && (
                <Text size="sm" c="dimmed">
                  Up next: {upNext.join(' → ')}
                </Text>
              )}
              {!canPick && (
                <Text size="xs" c="dimmed">
                  Waiting for {draft.current_team_name}&apos;s coach or an admin to pick.
                </Text>
              )}
            </div>
          </Group>
        ) : (
          <Group justify="space-between">
            <Title order={3}>Draft complete</Title>
            <Text size="sm" c="dimmed">
              {allPicks.length} picks over {rounds} round{rounds === 1 ? '' : 's'}
            </Text>
          </Group>
        )}
      </Paper>

      {/* Draft board: a column per team in draft order, a row per round */}
      <div>
        <Title order={4} mb={4}>
          Draft board
        </Title>
        <Table.ScrollContainer minWidth={n * 132 + 56}>
          <Table withTableBorder withColumnBorders style={{ tableLayout: 'fixed' }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={56}>Rd</Table.Th>
                {order.map((o) => (
                  <Table.Th key={o.team_id}>
                    <Group gap={6} wrap="nowrap">
                      <TeamSwatch color={colorOf(o.team_id)} />
                      <Text size="sm" fw={700} truncate>
                        {o.team_name}
                      </Text>
                    </Group>
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {Array.from({ length: rounds }, (_, r) => r + 1).map((round) => (
                <Table.Tr key={round}>
                  <Table.Td>
                    <Text size="sm" fw={700}>
                      {round} {round % 2 === 0 ? '←' : '→'}
                    </Text>
                  </Table.Td>
                  {order.map((o, slot) => {
                    const pickNumber = (round - 1) * n + (round % 2 === 0 ? n - slot : slot + 1)
                    if (pickNumber > totalPicks) return <Table.Td key={o.team_id} />
                    const pick = pickByNumber.get(pickNumber)
                    const onClock = live && pickNumber === draft.current_pick_number
                    return (
                      <Table.Td
                        key={o.team_id}
                        p={6}
                        style={onClock ? { outline: '2px solid var(--mantine-color-gold-4)', outlineOffset: -2 } : undefined}
                      >
                        <BoardCell pickNumber={pickNumber} pick={pick} onClock={onClock} />
                      </Table.Td>
                    )
                  })}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>

      <Grid gap="lg">
        {/* Player pool */}
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Group justify="space-between" align="flex-end" mb="xs">
            <Title order={4}>Player pool ({shownPool.length})</Title>
            <Group gap="xs">
              <TextInput
                size="xs"
                placeholder="Search players"
                aria-label="Search players"
                value={search}
                onChange={(e) => setSearch(e.currentTarget.value)}
              />
              <Select
                size="xs"
                w={170}
                aria-label="Position"
                placeholder="All positions"
                data={positions}
                value={position}
                onChange={setPosition}
                clearable
              />
              <Select
                size="xs"
                w={150}
                aria-label="Sort by"
                data={[
                  { value: 'grade', label: 'Sort: grade' },
                  { value: 'name', label: 'Sort: name' },
                  { value: 'age', label: 'Sort: oldest' },
                ]}
                value={sort}
                onChange={(v) => v && setSort(v as PoolSort)}
                allowDeselect={false}
              />
            </Group>
          </Group>
          <ScrollArea.Autosize mah={560} type="auto">
            <Table striped highlightOnHover stickyHeader>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Player</Table.Th>
                  <Table.Th>Pos</Table.Th>
                  <Table.Th>Grade</Table.Th>
                  <Table.Th>Age</Table.Th>
                  {canPick && <Table.Th />}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {shownPool.map((p) => (
                  <Table.Tr key={p.id}>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {lastFirst(p)}
                        {p.nickname && (
                          <Text span c="dimmed" fw={400}>
                            {' '}
                            &quot;{p.nickname}&quot;
                          </Text>
                        )}
                      </Text>
                      {(requestNotes.get(p.id) ?? []).map((note) => (
                        <Text key={note} size="xs" c="dimmed">
                          {note}
                        </Text>
                      ))}
                    </Table.Td>
                    <Table.Td>{shortPosition(p.position)}</Table.Td>
                    <Table.Td>
                      <GradeBadge grade={p.grade} />
                    </Table.Td>
                    <Table.Td>{age(p.birth_date)?.toFixed(1) ?? '—'}</Table.Td>
                    {canPick && (
                      <Table.Td ta="right">
                        <Button
                          size="compact-sm"
                          loading={pickMutation.isPending && pickMutation.variables?.id === p.id}
                          disabled={pickMutation.isPending}
                          onClick={() => pickMutation.mutate(p)}
                        >
                          Draft
                        </Button>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {shownPool.length === 0 && (
              <Text size="sm" c="dimmed" p="md">
                {(pool ?? []).length === 0 ? 'The pool is empty.' : 'No players match.'}
              </Text>
            )}
          </ScrollArea.Autosize>
        </Grid.Col>

        {/* Teams and history */}
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Tabs defaultValue="teams">
            <Tabs.List>
              <Tabs.Tab value="teams">Teams</Tabs.Tab>
              <Tabs.Tab value="history">History ({allPicks.length})</Tabs.Tab>
            </Tabs.List>

            <Tabs.Panel value="teams" pt="sm">
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Team</Table.Th>
                    <Table.Th ta="right">Players</Table.Th>
                    <Table.Th ta="right">Avg</Table.Th>
                    <Table.Th ta="right">Goalies</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {order.map((o) => {
                    const t = overviewOf(o.team_id)
                    return (
                      <Table.Tr
                        key={o.team_id}
                        bg={o.team_id === shownTeamId ? 'var(--app-panel-bg)' : undefined}
                        style={{ cursor: 'pointer' }}
                        onClick={() => setTeamId(o.team_id)}
                      >
                        <Table.Td>
                          <UnstyledButton onClick={() => setTeamId(o.team_id)}>
                            <Group gap={6} wrap="nowrap">
                              <TeamSwatch color={colorOf(o.team_id)} />
                              <Text size="sm" fw={o.team_id === draft.current_team_id ? 700 : 400}>
                                {o.team_name}
                                {live && o.team_id === draft.current_team_id ? ' ⏱' : ''}
                              </Text>
                            </Group>
                          </UnstyledButton>
                        </Table.Td>
                        <Table.Td ta="right">{t?.players ?? '—'}</Table.Td>
                        <Table.Td ta="right">{t?.average != null ? t.average.toFixed(2) : '—'}</Table.Td>
                        <Table.Td ta="right">{t?.goalies ?? '—'}</Table.Td>
                      </Table.Tr>
                    )
                  })}
                </Table.Tbody>
              </Table>

              <Title order={5} mt="md" mb={4}>
                {order.find((o) => o.team_id === shownTeamId)?.team_name} — picks ({shownTeamPicks.length})
              </Title>
              {shownTeamPicks.length === 0 ? (
                <Text size="sm" c="dimmed">
                  No picks yet.
                </Text>
              ) : (
                <Table>
                  <Table.Tbody>
                    {shownTeamPicks.map((p) => (
                      <Table.Tr key={p.pick_number}>
                        <Table.Td w={84} style={{ whiteSpace: "nowrap" }}>
                          <Text size="xs" c="dimmed">
                            R{p.round} · #{p.pick_number}
                          </Text>
                        </Table.Td>
                        <Table.Td>{p.player_name}</Table.Td>
                        <Table.Td>{shortPosition(p.position)}</Table.Td>
                        <Table.Td>
                          <GradeBadge grade={p.grade} />
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Tabs.Panel>

            <Tabs.Panel value="history" pt="sm">
              {allPicks.length === 0 ? (
                <Text size="sm" c="dimmed">
                  No picks yet.
                </Text>
              ) : (
                <ScrollArea.Autosize mah={520} type="auto">
                  <Table striped>
                    <Table.Tbody>
                      {[...allPicks].reverse().map((p) => (
                        <Table.Tr key={p.pick_number}>
                          <Table.Td w={84} style={{ whiteSpace: "nowrap" }}>
                            <Text size="xs" c="dimmed">
                              #{p.pick_number} · R{p.round}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm" fw={600}>
                              {p.player_name}
                            </Text>
                            <Text size="xs" c="dimmed">
                              {p.team_name} · {shortPosition(p.position)}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <GradeBadge grade={p.grade} />
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </ScrollArea.Autosize>
              )}
            </Tabs.Panel>
          </Tabs>
        </Grid.Col>
      </Grid>

      {user?.is_admin && (
        <Group>
          {allPicks.length > 0 && (
            <Button variant="default" loading={undoMutation.isPending} onClick={() => undoMutation.mutate()}>
              Undo last pick
            </Button>
          )}
          <Button
            color="red"
            variant="outline"
            loading={deleteMutation.isPending}
            onClick={() =>
              confirm('Delete this draft’s order and pick history? Already-drafted players stay on their roster.') &&
              deleteMutation.mutate()
            }
          >
            Delete this draft
          </Button>
        </Group>
      )}
      {!live && allPicks.length === 0 && <Alert color="blue">This draft finished without any picks.</Alert>}
    </Stack>
  )
}

function TeamSwatch({ color, size = 12 }: { color: string | null; size?: number }) {
  return (
    <Box
      w={size}
      h={size}
      style={{
        flexShrink: 0,
        borderRadius: 3,
        background: color ?? 'transparent',
        border: '1px solid var(--mantine-color-default-border)',
      }}
    />
  )
}

function BoardCell({ pickNumber, pick, onClock }: { pickNumber: number; pick?: DraftPick; onClock: boolean }) {
  return (
    <Box mih={44}>
      <Text size="xs" c="dimmed">
        #{pickNumber}
      </Text>
      {pick ? (
        <>
          <Text size="sm" fw={600} lh={1.2} truncate title={pick.player_name}>
            {pick.player_name}
          </Text>
          <Group gap={6} mt={2}>
            <Text size="xs" c="dimmed">
              {shortPosition(pick.position)}
            </Text>
            <GradeBadge grade={pick.grade} />
          </Group>
        </>
      ) : (
        onClock && (
          <Text size="xs" fw={700} c="var(--app-heading)">
            On the clock
          </Text>
        )
      )}
    </Box>
  )
}
