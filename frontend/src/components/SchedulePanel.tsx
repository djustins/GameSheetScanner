import { useRef } from 'react'
import { Badge, Button, FileButton, Group, Stack, Table, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Papa from 'papaparse'
import { clearSchedule, getSchedule, importSchedule } from '../api/divisions'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

const REQUIRED_COLUMNS = ['Date', 'Home Team', 'Away Team']

export function SchedulePanel({ divisionId }: { divisionId: number }) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const resetRef = useRef<() => void>(null)

  const { data: schedule } = useQuery({ queryKey: ['schedule', divisionId], queryFn: () => getSchedule(divisionId) })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['schedule', divisionId] })
  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const importMutation = useMutation({
    mutationFn: (rows: Record<string, unknown>[]) => importSchedule(divisionId, rows),
    onSuccess: (result) => {
      invalidate()
      notifications.show({ message: `Saved ${result.saved} scheduled game(s) to the database.` })
      resetRef.current?.()
    },
    onError,
  })

  const clearMutation = useMutation({
    mutationFn: () => clearSchedule(divisionId),
    onSuccess: invalidate,
    onError,
  })

  function handleFile(file: File | null) {
    if (!file) return
    Papa.parse<Record<string, string>>(file, {
      header: true,
      skipEmptyLines: true,
      complete: (results) => {
        const columns = results.meta.fields ?? []
        const missing = REQUIRED_COLUMNS.filter((c) => !columns.includes(c))
        if (missing.length > 0) {
          notifications.show({ color: 'red', message: `CSV is missing expected column(s): ${missing.join(', ')}` })
          return
        }
        const rows = results.data.map((row) => ({
          order: row['Order'] || null,
          round: row['Round'] || null,
          game_date: row['Date'],
          home_team: row['Home Team'],
          away_team: row['Away Team'],
          start_time: row['Start Time'] || null,
          end_time: row['End Time'] || null,
          location: row['Location'] || null,
          field: row['Field'] || null,
        }))
        importMutation.mutate(rows)
      },
      error: (err) => notifications.show({ color: 'red', message: `Couldn't read that CSV: ${err.message}` }),
    })
  }

  const total = schedule?.length ?? 0
  const done = schedule?.filter((r) => r.accounted_for).length ?? 0

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Upload this division&apos;s official schedule (a CSV with Date/Home Team/Away Team columns).
      </Text>
      {user?.is_admin && (
        <Group>
          <FileButton resetRef={resetRef} onChange={handleFile} accept=".csv">
            {(props) => (
              <Button {...props} loading={importMutation.isPending} variant="light">
                Upload schedule CSV
              </Button>
            )}
          </FileButton>
          <Button
            color="red"
            variant="outline"
            loading={clearMutation.isPending}
            onClick={() => confirm("Remove this division's entire saved schedule?") && clearMutation.mutate()}
          >
            Clear
          </Button>
        </Group>
      )}

      {total > 0 ? (
        <Text size="sm" c="dimmed">
          {done}/{total} scheduled games played so far.
        </Text>
      ) : (
        <Text size="sm" c="dimmed">
          No schedule uploaded yet for this division.
        </Text>
      )}

      {total > 0 && (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Date</Table.Th>
              <Table.Th>Home</Table.Th>
              <Table.Th>Away</Table.Th>
              <Table.Th>Round</Table.Th>
              <Table.Th>Location</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {schedule!.map((row) => (
              <Table.Tr key={row.id}>
                <Table.Td>{row.game_date}</Table.Td>
                <Table.Td>{row.home_team}</Table.Td>
                <Table.Td>{row.away_team}</Table.Td>
                <Table.Td>{row.round ?? '—'}</Table.Td>
                <Table.Td>{row.location ?? '—'}</Table.Td>
                <Table.Td>
                  {row.accounted_for ? (
                    <Badge color="green" variant="light">
                      Played
                      {row.result ? ` ${row.result.home_score ?? '?'}–${row.result.away_score ?? '?'}` : ''}
                    </Badge>
                  ) : (
                    <Badge color="gray" variant="light">
                      Not played
                    </Badge>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
