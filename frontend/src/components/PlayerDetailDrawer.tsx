import { useState } from 'react'
import { Badge, Button, Drawer, Group, Loader, Select, Stack, Table, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deletePlayer, getPlayerHistory, getSiblings } from '../api/players'
import { listParents, setPlayerParent } from '../api/parents'
import type { Player } from '../api/types'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { coachLabel, divisionLabel } from '../utils/format'

interface Props {
  player: Player | null
  onClose: () => void
}

export function PlayerDetailDrawer({ player, onClose }: Props) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [parentPickId, setParentPickId] = useState<string | null>(null)

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

  const { data: parents } = useQuery({ queryKey: ['parents'], queryFn: listParents })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const setParentMutation = useMutation({
    mutationFn: (parentId: number | null) => setPlayerParent(player!.id, parentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['siblings', player?.id] })
      setParentPickId(null)
    },
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deletePlayer(player!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['players'] })
      queryClient.invalidateQueries({ queryKey: ['division-players'] })
      onClose()
    },
    onError,
  })

  return (
    <Drawer opened={player != null} onClose={onClose} title={player?.name} position="right" size="xl">
      {player && (
        <Stack>
          <Group>
            {player.birth_date && <Text size="sm">Born {player.birth_date}</Text>}
          </Group>

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
      )}
    </Drawer>
  )
}
