import { useQueries, useQuery } from '@tanstack/react-query'
import { Table, Text } from '@mantine/core'
import { getSeasonGrades } from '../api/divisions'
import { listRoster, listTeamCoaches } from '../api/teams'
import type { Team } from '../api/types'
import { coachLabel } from '../utils/format'
import { gradeBreakdown } from '../utils/grades'

export function TeamsOverviewTable({
  divisionId,
  teams,
  onRowClick,
}: {
  divisionId: number
  teams: Team[]
  onRowClick?: (team: Team) => void
}) {
  const { data: seasonGrades } = useQuery({
    queryKey: ['season-grades', divisionId],
    queryFn: () => getSeasonGrades(divisionId),
  })

  const rosterQueries = useQueries({
    queries: teams.map((t) => ({ queryKey: ['roster', t.id], queryFn: () => listRoster(t.id) })),
  })
  const coachQueries = useQueries({
    queries: teams.map((t) => ({ queryKey: ['team-coaches', t.id], queryFn: () => listTeamCoaches(t.id) })),
  })

  return (
    <Table striped highlightOnHover>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Team</Table.Th>
          <Table.Th>Color</Table.Th>
          <Table.Th>Coach(es)</Table.Th>
          <Table.Th>Players</Table.Th>
          <Table.Th>Avg</Table.Th>
          <Table.Th>Breakdown</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {teams.map((t, i) => {
          const roster = rosterQueries[i]?.data ?? []
          const coaches = coachQueries[i]?.data ?? []
          const grades = roster
            .filter((r) => r.player_id != null)
            .map((r) => seasonGrades?.[String(r.player_id)])
          const { gradedCount, average, breakdown } = gradeBreakdown(grades)
          return (
            <Table.Tr key={t.id} style={onRowClick ? { cursor: 'pointer' } : undefined} onClick={() => onRowClick?.(t)}>
              <Table.Td>{t.name}</Table.Td>
              <Table.Td>{t.color ?? '—'}</Table.Td>
              <Table.Td>{coaches.length ? coaches.map(coachLabel).join(', ') : '—'}</Table.Td>
              <Table.Td>
                {roster.length} added · {gradedCount} graded
              </Table.Td>
              <Table.Td>{average != null ? average.toFixed(1) : '—'}</Table.Td>
              <Table.Td>
                <Text size="sm">{breakdown}</Text>
              </Table.Td>
            </Table.Tr>
          )
        })}
      </Table.Tbody>
    </Table>
  )
}
