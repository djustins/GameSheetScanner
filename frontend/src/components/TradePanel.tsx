import { useState } from 'react'
import {
  Accordion,
  Alert,
  Button,
  Checkbox,
  Group,
  MultiSelect,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { previewTrade, trade } from '../api/draft'
import { listDivisionTeams } from '../api/divisions'
import { listRoster, listTeamCoaches } from '../api/teams'
import { ApiError } from '../api/client'
import type { TradeBody, TradeEffect } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useAccess } from '../auth/access'
import { formatAge } from '../utils/format'

// A trade is blocked (unless "do it anyway") when it splits siblings or a hard
// request, or puts a "do not play with" pair together -- core.trade_blockers.
function isBlocker(e: TradeEffect): boolean {
  return (!e.joined && (e.kind === 'sibling' || e.kind === 'hard')) || (e.joined && e.kind === 'avoid')
}

function effectLabel(e: TradeEffect): string {
  if (e.kind === 'avoid') {
    return `${e.joined ? '⛔ puts together' : '✅ separates'} ${e.name} & ${e.other_name} (do not play with)`
  }
  const icon = e.joined ? '✅ joins' : isBlocker(e) ? '⛔ splits' : '❌ splits'
  return `${icon} ${e.name} & ${e.other_name} (${e.kind})`
}

/** Swap players between two of the division's teams in one step, with a
 * before/after look at both teams and every request or sibling pair the trade
 * joins or splits. Mirrors the Streamlit app's trade panel. */
export function TradePanel({ divisionId }: { divisionId: number }) {
  const { user } = useAuth()
  const { readOnly } = useAccess()
  const queryClient = useQueryClient()
  const [teamA, setTeamA] = useState<string | null>(null)
  const [teamB, setTeamB] = useState<string | null>(null)
  const [fromA, setFromA] = useState<string[]>([])
  const [fromB, setFromB] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [anyway, setAnyway] = useState(false)

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId),
  })
  const coachQueries = useQueries({
    queries: (teams ?? []).map((t) => ({ queryKey: ['team-coaches', t.id], queryFn: () => listTeamCoaches(t.id) })),
  })
  const teamLabel = (id: number) => {
    const i = (teams ?? []).findIndex((t) => t.id === id)
    const coaches = coachQueries[i]?.data ?? []
    const name = teams?.[i]?.name ?? '?'
    return `${name} (${coaches.length ? coaches.map((c) => c.name).join(', ') : 'no coach'})`
  }

  const { data: rosterA } = useQuery({
    queryKey: ['roster', Number(teamA)],
    queryFn: () => listRoster(Number(teamA)),
    enabled: !!teamA,
  })
  const { data: rosterB } = useQuery({
    queryKey: ['roster', Number(teamB)],
    queryFn: () => listRoster(Number(teamB)),
    enabled: !!teamB,
  })

  const body: TradeBody | null =
    teamA && teamB && (fromA.length || fromB.length)
      ? {
          team_a: Number(teamA),
          from_a: fromA.map(Number),
          team_b: Number(teamB),
          from_b: fromB.map(Number),
          note: user?.is_admin ? note.trim() || null : null,
          allow_split: anyway,
        }
      : null

  const { data: preview } = useQuery({
    queryKey: ['trade-preview', divisionId, body?.team_a, body?.team_b, fromA, fromB],
    queryFn: () => previewTrade(divisionId, body!),
    enabled: !!body,
  })

  const tradeMutation = useMutation({
    mutationFn: () => trade(divisionId, body!),
    onSuccess: () => {
      notifications.show({ color: 'green', message: `Traded between ${teamLabel(Number(teamA))} and ${teamLabel(Number(teamB))}.` })
      setFromA([])
      setFromB([])
      setNote('')
      setAnyway(false)
      for (const key of [['roster'], ['auto-draft-results'], ['teams-overview'], ['division-requests'], ['trade-preview']]) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: (err) =>
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Trade failed.' }),
  })

  if (!teams || teams.length < 2) return null

  const blockers = (preview?.effects ?? []).filter(isBlocker)
  const playerOptions = (roster: typeof rosterA) =>
    (roster ?? []).filter((e) => e.player_id != null).map((e) => ({ value: String(e.player_id), label: e.name }))

  return (
    <Accordion variant="contained">
      <Accordion.Item value="trade">
        <Accordion.Control>🔁 Trade players between teams</Accordion.Control>
        <Accordion.Panel>
          <Stack>
            <Group grow align="flex-start">
              <Stack gap="xs">
                <Select
                  label="Team"
                  data={teams.map((t) => ({ value: String(t.id), label: teamLabel(t.id) }))}
                  value={teamA}
                  onChange={(v) => {
                    setTeamA(v)
                    setFromA([])
                    if (v === teamB) setTeamB(null)
                  }}
                />
                {teamA && (
                  <MultiSelect
                    label={`From ${teams.find((t) => String(t.id) === teamA)?.name}`}
                    data={playerOptions(rosterA)}
                    value={fromA}
                    onChange={setFromA}
                    searchable
                  />
                )}
              </Stack>
              <Stack gap="xs">
                <Select
                  label="Trades with"
                  data={teams.filter((t) => String(t.id) !== teamA).map((t) => ({ value: String(t.id), label: teamLabel(t.id) }))}
                  value={teamB}
                  onChange={(v) => {
                    setTeamB(v)
                    setFromB([])
                  }}
                  disabled={!teamA}
                />
                {teamB && (
                  <MultiSelect
                    label={`From ${teams.find((t) => String(t.id) === teamB)?.name}`}
                    data={playerOptions(rosterB)}
                    value={fromB}
                    onChange={setFromB}
                    searchable
                  />
                )}
              </Stack>
            </Group>

            {!body && (
              <Text size="sm" c="dimmed">
                Pick the player(s) going each way — one side can be empty for a one-way transfer.
              </Text>
            )}

            {body && preview && (
              <>
                <Table striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Team</Table.Th>
                      <Table.Th />
                      <Table.Th>Players</Table.Th>
                      <Table.Th>Skill</Table.Th>
                      <Table.Th>Avg skill</Table.Th>
                      <Table.Th>Goalies</Table.Th>
                      <Table.Th>Avg age</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {[body.team_a, body.team_b].flatMap((t) =>
                      (['now', 'after'] as const).map((when) => {
                        const b = preview[when][String(t)]
                        return (
                          <Table.Tr key={`${t}-${when}`}>
                            <Table.Td>{teamLabel(t)}</Table.Td>
                            <Table.Td>{when === 'now' ? 'Now' : 'After'}</Table.Td>
                            <Table.Td>{b?.players}</Table.Td>
                            <Table.Td>{b?.skill}</Table.Td>
                            <Table.Td>{b?.avg_skill ?? '—'}</Table.Td>
                            <Table.Td>{b?.goalies}</Table.Td>
                            <Table.Td>{formatAge(b?.avg_age)}</Table.Td>
                          </Table.Tr>
                        )
                      })
                    )}
                  </Table.Tbody>
                </Table>

                {preview.effects.length ? (
                  <Stack gap={2}>
                    <Text size="sm" fw={600}>
                      Requests &amp; siblings this trade changes
                    </Text>
                    {preview.effects.map((e, i) => (
                      <Text size="sm" key={i}>
                        {effectLabel(e)}
                      </Text>
                    ))}
                  </Stack>
                ) : (
                  <Text size="sm" c="dimmed">
                    No play-with requests or siblings are joined or split by this trade.
                  </Text>
                )}

                {user?.is_admin && (
                  <TextInput label="Reason (admin only)" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
                )}
                {blockers.length > 0 && (
                  <Alert color="orange">
                    <Checkbox
                      label="Do it anyway — siblings and hard requests are normally kept together, and a do-not-play-with pair apart."
                      checked={anyway}
                      onChange={(e) => setAnyway(e.currentTarget.checked)}
                    />
                  </Alert>
                )}
                <Button
                  w="fit-content"
                  disabled={readOnly || (blockers.length > 0 && !anyway)}
                  loading={tradeMutation.isPending}
                  onClick={() => tradeMutation.mutate()}
                >
                  Confirm trade
                </Button>
              </>
            )}
          </Stack>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  )
}
