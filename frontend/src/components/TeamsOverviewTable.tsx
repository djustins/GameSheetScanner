import { useQuery } from '@tanstack/react-query'
import { Box, Group, Table, Text } from '@mantine/core'
import { getTeamsOverview } from '../api/divisions'
import type { Team, TeamOverview } from '../api/types'
import { formatAge } from '../utils/format'
import { GRADE_TIERS } from '../utils/grades'

// This age group's own grades first, then move-ups (A*, B*...) last -- they're
// weighted below the team's own grades, as a D (see core.teams_overview).
function breakdown(t: TeamOverview): string {
  const own = GRADE_TIERS.filter((g) => t.grades[g]).map((g) => `${g}: ${t.grades[g]}`)
  const moveUps = GRADE_TIERS.filter((g) => t.move_up_grades[g]).map((g) => `${g}*: ${t.move_up_grades[g]}`)
  return [...own, ...moveUps].join(', ') || '—'
}

export function TeamsOverviewTable({
  divisionId,
  teams,
  onRowClick,
}: {
  divisionId: number
  teams: Team[]
  onRowClick?: (team: Team) => void
}) {
  const { data: overview } = useQuery({
    queryKey: ['teams-overview', divisionId],
    queryFn: () => getTeamsOverview(divisionId),
  })
  const byId = new Map((overview ?? []).map((o) => [o.team_id, o]))
  const anyMoveUps = (overview ?? []).some((o) => Object.keys(o.move_up_grades).length > 0)

  return (
    <>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Team</Table.Th>
            <Table.Th>Coach(es)</Table.Th>
            <Table.Th>Players</Table.Th>
            <Table.Th>Avg</Table.Th>
            <Table.Th>Breakdown</Table.Th>
            <Table.Th>Avg age</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {teams.map((team) => {
            const t = byId.get(team.id)
            const moveUps = t ? Object.values(t.move_up_grades).reduce((a, b) => a + b, 0) : 0
            return (
              <Table.Tr
                key={team.id}
                style={onRowClick ? { cursor: 'pointer' } : undefined}
                onClick={() => onRowClick?.(team)}
              >
                <Table.Td>
                  <Group gap={8} wrap="nowrap">
                    <Box
                      w={14}
                      h={14}
                      style={{
                        borderRadius: 3,
                        background: team.color ?? 'transparent',
                        border: '1px solid var(--mantine-color-default-border)',
                        flexShrink: 0,
                      }}
                    />
                    {team.name}
                  </Group>
                </Table.Td>
                <Table.Td>{t?.coaches.length ? t.coaches.join(', ') : '—'}</Table.Td>
                <Table.Td>
                  {t ? `${t.players} added · ${t.graded} graded${moveUps ? ` (${moveUps}*)` : ''}` : '…'}
                </Table.Td>
                <Table.Td>{t?.average != null ? t.average.toFixed(1) : '—'}</Table.Td>
                <Table.Td>
                  <Text size="sm">{t ? breakdown(t) : '…'}</Text>
                  {t && t.players > 0 && t.goalies === 0 && (
                    <Text size="sm" fw={700} c="orange">
                      ⚠️ No goalie
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{formatAge(t?.avg_age)}</Table.Td>
              </Table.Tr>
            )
          })}
        </Table.Tbody>
      </Table>
      {anyMoveUps && (
        <Text size="xs" c="dimmed" mt={4}>
          * move-up grade (from a different age group) — shown as is, but weighted as a D in the average, the way
          Auto-Draft and trades weight it.
        </Text>
      )}
    </>
  )
}
