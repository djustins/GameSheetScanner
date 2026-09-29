import { Alert, Button, Group, Progress, Stack, Table, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getSchedule } from '../api/divisions'
import type { ScheduleRow } from '../api/types'

function resultText(row: ScheduleRow) {
  const r = row.result
  if (!r) return '—'
  return `${r.home_team} ${r.home_score ?? '?'}–${r.away_score ?? '?'} ${r.away_team}`
}

interface Props {
  divisionId: number
  // Omitted when the viewer's role can't open Import Scoresheets.
  onImportScoresheets?: () => void
}

export function ScheduleResultsPanel({ divisionId, onImportScoresheets }: Props) {
  const { data: schedule, isLoading } = useQuery({
    queryKey: ['schedule', divisionId],
    queryFn: () => getSchedule(divisionId),
  })

  const total = schedule?.length ?? 0
  const done = schedule?.filter((r) => r.accounted_for).length ?? 0
  // A stored game between the same two teams under a date that matches no
  // scheduled date for that matchup is almost always this game with a
  // mis-typed date (usually the year), not a real gap -- see
  // game_sheet_core.list_schedule's possible_matches.
  const flagged = (schedule ?? []).filter((r) => !r.accounted_for && r.possible_matches.length > 0)

  return (
    <Stack>
      <Group justify="space-between" align="flex-start">
        <Text size="sm" c="dimmed" maw={640}>
          The season schedule for the Working Division, compared against games actually entered — showing the
          score for every game that&apos;s been played. Upload or manage a division&apos;s schedule from the
          Divisions page.
        </Text>
        {onImportScoresheets && (
          <Button variant="light" onClick={onImportScoresheets}>
            Import Scoresheets
          </Button>
        )}
      </Group>

      {!isLoading && total === 0 && (
        <Alert color="blue">No schedule uploaded yet for this division — add one from the Divisions page.</Alert>
      )}

      {total > 0 && (
        <>
          <div>
            <Text size="sm" mb={4}>
              {done}/{total} scheduled games played
            </Text>
            <Progress value={(done / total) * 100} />
          </div>

          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Round</Table.Th>
                <Table.Th>Date</Table.Th>
                <Table.Th>Away Team</Table.Th>
                <Table.Th>Home Team</Table.Th>
                <Table.Th>Start Time</Table.Th>
                <Table.Th>Location</Table.Th>
                <Table.Th>Result</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {schedule!.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>{row.round ?? '—'}</Table.Td>
                  <Table.Td>{row.game_date}</Table.Td>
                  <Table.Td>{row.away_team}</Table.Td>
                  <Table.Td>{row.home_team}</Table.Td>
                  <Table.Td>{row.start_time ?? '—'}</Table.Td>
                  <Table.Td>{row.location ?? '—'}</Table.Td>
                  <Table.Td>{resultText(row)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          {done === total ? (
            <Alert color="green">Every scheduled game has been entered.</Alert>
          ) : (
            flagged.length > 0 && (
              <Stack gap="xs">
                <Text size="sm" c="dimmed">
                  Some of these may already be entered under the wrong date (a mis-typed year is the usual culprit)
                  rather than genuinely missing:
                </Text>
                {flagged.flatMap((r) =>
                  r.possible_matches.map((m) => (
                    <Alert key={`${r.id}-${m.id}`} color="yellow">
                      <b>
                        {r.away_team} @ {r.home_team}
                      </b>
                      , scheduled for {r.game_date} — game #{m.id} between these two teams is on file dated{' '}
                      <b>{m.game_date}</b> instead. Check/fix its date in Manage Games; it&apos;ll show as accounted
                      for here automatically once corrected.
                    </Alert>
                  ))
                )}
              </Stack>
            )
          )}
        </>
      )}
    </Stack>
  )
}
