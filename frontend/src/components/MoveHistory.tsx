import { Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getDivisionMoves } from '../api/draft'

// The API's timestamp is text as stored; show it as a local date and time
// when the browser can read it, else as it came.
function when(stamp: string): string {
  const parsed = new Date(stamp.includes('T') ? stamp : stamp.replace(' ', 'T'))
  return Number.isNaN(parsed.getTime())
    ? stamp
    : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** Admin-only: every player moved between teams in the division, by trade or
 * one at a time, newest first, with the reason when one was given. */
export function MoveHistory({ divisionId }: { divisionId: number }) {
  const { data: moves } = useQuery({
    queryKey: ['division-moves', divisionId],
    queryFn: () => getDivisionMoves(divisionId),
  })

  return (
    <Stack gap="xs">
      <Title order={4}>Move history</Title>
      <Text size="sm" c="dimmed">
        Every player moved between teams in this division, by trade or one at a time. Only admins see this.
      </Text>
      {moves && moves.length === 0 && (
        <Text size="sm" c="dimmed">
          No moves recorded yet.
        </Text>
      )}
      {moves && moves.length > 0 && (
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>When</Table.Th>
              <Table.Th>Player</Table.Th>
              <Table.Th>From</Table.Th>
              <Table.Th>To</Table.Th>
              <Table.Th>Reason</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {moves.map((m) => (
              <Table.Tr key={m.id}>
                <Table.Td style={{ whiteSpace: 'nowrap' }}>{when(m.created_at)}</Table.Td>
                <Table.Td>{m.player}</Table.Td>
                <Table.Td>{m.from_team ?? '—'}</Table.Td>
                <Table.Td>{m.to_team ?? '—'}</Table.Td>
                <Table.Td>{m.note ?? '—'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  )
}
