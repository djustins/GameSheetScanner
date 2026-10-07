import { useState } from 'react'
import { Alert, Button, Group, Select, Stack, Table, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getDraftPool } from '../api/draft'
import { listDivisionTeams } from '../api/divisions'
import { addRosterEntry } from '../api/teams'
import { ApiError } from '../api/client'
import type { DraftPoolPlayer } from '../api/types'
import { useAccess } from '../auth/access'

/** Everyone registered in the division who isn't on one of its teams yet (the
 * draft pool), each with a quick way onto a team without running a draft. */
export function UnplacedPlayers({ divisionId }: { divisionId: number }) {
  const { readOnly } = useAccess()
  const queryClient = useQueryClient()
  // The team picked on each player's row, by player id.
  const [teamFor, setTeamFor] = useState<Record<number, string | null>>({})

  const { data: pool, isLoading } = useQuery({
    queryKey: ['draft-pool', divisionId],
    queryFn: () => getDraftPool(divisionId),
  })
  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId),
  })
  const teamOptions = (teams ?? []).map((t) => ({ value: String(t.id), label: t.name }))

  const addMutation = useMutation({
    // A blank number gets the team's next placeholder (TBD3), as a draft pick does.
    mutationFn: (player: DraftPoolPlayer) =>
      addRosterEntry(Number(teamFor[player.id]), { number: '', name: player.name, player_id: player.id }),
    onSuccess: (entry, player) => {
      const team = teamOptions.find((t) => t.value === teamFor[player.id])?.label
      notifications.show({ color: 'green', message: `${player.name} added to ${team} as #${entry.number}.` })
      for (const key of [['draft-pool', divisionId], ['roster'], ['division-players'], ['teams-overview'], ['division-requests']]) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: (err) =>
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Could not add the player.' }),
  })

  if (isLoading) return null
  if ((pool ?? []).length === 0) {
    return <Alert color="green">Everyone registered in this division is on a team.</Alert>
  }

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {pool!.length} registered player{pool!.length === 1 ? '' : 's'} not on a team yet. Adding one here gives them a
        placeholder number to replace on the team&apos;s roster.
      </Text>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Player</Table.Th>
            <Table.Th>Birth date</Table.Th>
            <Table.Th>Registered position</Table.Th>
            {!readOnly && <Table.Th>Add to team</Table.Th>}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {pool!.map((p) => (
            <Table.Tr key={p.id}>
              <Table.Td>
                {p.last_name ? `${p.last_name}, ${p.first_name}` : p.first_name}
                {p.nickname && (
                  <Text span c="dimmed">
                    {' '}
                    &quot;{p.nickname}&quot;
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{p.birth_date ?? '—'}</Table.Td>
              <Table.Td>{p.position ?? '—'}</Table.Td>
              {!readOnly && (
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Select
                      size="xs"
                      w={180}
                      aria-label={`Team for ${p.name}`}
                      placeholder={teamOptions.length ? 'Choose a team' : 'No teams yet'}
                      data={teamOptions}
                      value={teamFor[p.id] ?? null}
                      onChange={(v) => setTeamFor((prev) => ({ ...prev, [p.id]: v }))}
                      disabled={teamOptions.length === 0}
                    />
                    <Button
                      size="xs"
                      disabled={!teamFor[p.id]}
                      loading={addMutation.isPending && addMutation.variables?.id === p.id}
                      onClick={() => addMutation.mutate(p)}
                    >
                      Add
                    </Button>
                  </Group>
                </Table.Td>
              )}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  )
}
