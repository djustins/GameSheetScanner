import { Badge, Drawer, Group, Loader, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getPlayerHistory } from '../api/players'
import type { Player } from '../api/types'
import { coachLabel, divisionLabel } from '../utils/format'

interface Props {
  player: Player | null
  onClose: () => void
}

export function PlayerDetailDrawer({ player, onClose }: Props) {
  const { data: history, isLoading } = useQuery({
    queryKey: ['player-history', player?.id],
    queryFn: () => getPlayerHistory(player!.id),
    enabled: player != null,
  })

  return (
    <Drawer opened={player != null} onClose={onClose} title={player?.name} position="right" size="xl">
      {player && (
        <>
          <Group mb="md">
            {player.birth_date && <Text size="sm">Born {player.birth_date}</Text>}
          </Group>
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
        </>
      )}
    </Drawer>
  )
}
