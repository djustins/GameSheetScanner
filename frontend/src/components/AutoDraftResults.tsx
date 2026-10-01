import { useState } from 'react'
import { Alert, Button, Select, Stack, Table, Text, Textarea, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getAutoDraftResults, getDraftNotes, saveDraftNotes } from '../api/draft'
import { ApiError } from '../api/client'
import type { AutoDraftRow } from '../api/types'
import { useAccess } from '../auth/access'

const SKILL: Record<string, number> = { A: 4, B: 3, C: 2, 'D/New': 1 }

function averageAge(birthDates: (string | null)[]): string {
  const days = birthDates.filter((d): d is string => !!d).map((d) => Date.parse(d))
  if (!days.length) return '—'
  const born = new Date(days.reduce((a, b) => a + b, 0) / days.length)
  const now = new Date()
  let months = (now.getFullYear() - born.getFullYear()) * 12 + now.getMonth() - born.getMonth()
  if (now.getDate() < born.getDate()) months -= 1
  const anchor = new Date(born.getFullYear(), born.getMonth() + months, Math.min(born.getDate(), 28))
  const d = Math.round((now.getTime() - anchor.getTime()) / 86400000)
  return `${Math.floor(months / 12)}y ${months % 12}m ${d}d`
}

function requestText(r: AutoDraftRow): string {
  return r.requests
    .map((q) =>
      q.avoid
        ? `${q.together ? '❌' : '✅'} 🚫 ${q.name} (do not play with)`
        : `${q.together ? '✅' : '❌'} ${q.name}${q.hard ? ' (hard)' : ''}`
    )
    .join('; ')
}

/** The division's current auto-draft as a per-team summary and a table of
 * every player it placed, plus the run's saved warnings. Hidden when there's
 * no run. */
export function AutoDraftResults({ divisionId }: { divisionId: number }) {
  const [teamFilter, setTeamFilter] = useState<string | null>('All Teams')
  const { data } = useQuery({
    queryKey: ['auto-draft-results', divisionId],
    queryFn: () => getAutoDraftResults(divisionId),
  })
  if (!data?.run || data.rows.length === 0) return null

  const teams = [...new Set(data.rows.map((r) => r.team))].sort()
  const shown = data.rows.filter((r) => teamFilter === 'All Teams' || r.team === teamFilter)

  return (
    <Stack>
      <Title order={4}>Auto-Draft results</Title>
      <Text size="sm" c="dimmed">
        Run {data.run.created_at.slice(0, 16)} · {data.rows.length} player(s) placed.
      </Text>
      {data.run.warnings.length > 0 && (
        <Alert color="yellow" title={`Warnings (${data.run.warnings.length})`}>
          <Stack gap={2}>
            {data.run.warnings.map((w, i) => (
              <Text size="sm" key={i}>
                • {w}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Team</Table.Th>
            <Table.Th>Coach</Table.Th>
            <Table.Th>Players</Table.Th>
            <Table.Th>Goalies</Table.Th>
            <Table.Th>Avg skill</Table.Th>
            <Table.Th>A / B / C / D-New</Table.Th>
            <Table.Th>Avg age</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {teams.map((team) => {
            const rows = data.rows.filter((r) => r.team === team)
            const grades = rows.map((r) => r.draft_grade)
            return (
              <Table.Tr key={team}>
                <Table.Td>{team}</Table.Td>
                <Table.Td>{rows[0].coach ?? '— none —'}</Table.Td>
                <Table.Td>{rows.length}</Table.Td>
                <Table.Td>{rows.filter((r) => r.goalie).length}</Table.Td>
                <Table.Td>{(grades.reduce((s, g) => s + SKILL[g], 0) / grades.length).toFixed(2)}</Table.Td>
                <Table.Td>{['A', 'B', 'C', 'D/New'].map((g) => grades.filter((x) => x === g).length).join(' / ')}</Table.Td>
                <Table.Td>{averageAge(rows.map((r) => r.birth_date))}</Table.Td>
              </Table.Tr>
            )
          })}
        </Table.Tbody>
      </Table>

      <Select
        label="Team"
        data={['All Teams', ...teams]}
        value={teamFilter}
        onChange={setTeamFilter}
        allowDeselect={false}
        w={260}
      />
      <Table.ScrollContainer minWidth={900}>
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Team</Table.Th>
              <Table.Th>Coach</Table.Th>
              <Table.Th>#</Table.Th>
              <Table.Th>Player</Table.Th>
              <Table.Th>Grade</Table.Th>
              <Table.Th>Drafted as</Table.Th>
              <Table.Th>Birth date</Table.Th>
              <Table.Th>Position</Table.Th>
              <Table.Th>Requests</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((r) => (
              <Table.Tr key={r.player_id}>
                <Table.Td>{r.team}</Table.Td>
                <Table.Td>{r.coach ?? '—'}</Table.Td>
                <Table.Td>{r.number}</Table.Td>
                <Table.Td>
                  {r.name}
                  {r.parent_coach ? `  👤 parent: ${r.parent_coach}` : ''}
                </Table.Td>
                <Table.Td>{r.grade ?? '—'}</Table.Td>
                <Table.Td>{r.draft_grade}</Table.Td>
                <Table.Td>{r.birth_date ?? '—'}</Table.Td>
                <Table.Td>
                  {r.goalie ? '🥅 ' : ''}
                  {r.position ?? '—'}
                </Table.Td>
                <Table.Td>{requestText(r)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  )
}

/** Free-form notes for this division's draft, shared with everyone (and with
 * the Streamlit app). */
export function DraftNotes({ divisionId }: { divisionId: number }) {
  const { readOnly } = useAccess()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const { data } = useQuery({ queryKey: ['draft-notes', divisionId], queryFn: () => getDraftNotes(divisionId) })
  const saved = data?.notes ?? ''
  const value = draft ?? saved
  const mutation = useMutation({
    mutationFn: () => saveDraftNotes(divisionId, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['draft-notes', divisionId] })
      setDraft(null)
      notifications.show({ color: 'green', message: 'Draft notes saved.' })
    },
    onError: (err) =>
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Could not save notes.' }),
  })
  return (
    <Stack gap="xs">
      <Title order={4}>Draft notes</Title>
      <Textarea
        autosize
        minRows={5}
        placeholder="Anything to remember about this draft…"
        value={value}
        onChange={(e) => setDraft(e.currentTarget.value)}
        disabled={readOnly}
      />
      <Button w="fit-content" disabled={readOnly || value === saved} loading={mutation.isPending} onClick={() => mutation.mutate()}>
        Save notes
      </Button>
    </Stack>
  )
}
