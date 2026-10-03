import { useState } from 'react'
import { Alert, Button, Group, Paper, Select, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import { listPlayers, registerPlayerInDivision, setRegistrationPosition } from '../api/players'
import { ApiError } from '../api/client'
import { useAccess } from '../auth/access'
import { divisionSeasonLabel } from '../utils/format'

const POSITIONS = ['Forward', 'Defense', 'Forward or Defense', 'Goalie']

/** Register one existing player in this division -- e.g. a late signup, or a
 * player moving up. Another age group this season is kept (a player can play
 * in two); a registration from another season is replaced. */
export function RegisterPlayerPanel({ divisionId }: { divisionId: number }) {
  const { readOnly } = useAccess()
  const queryClient = useQueryClient()
  const [playerId, setPlayerId] = useState<string | null>(null)
  const [position, setPosition] = useState<string | null>(null)

  const { data: players } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const division = divisions?.find((d) => d.id === divisionId)
  const labelOf = (id: number) => {
    const d = divisions?.find((x) => x.id === id)
    return d ? divisionSeasonLabel(d) : `division ${id}`
  }

  const options = (players ?? [])
    .filter((p) => !p.division_ids.includes(divisionId))
    .map((p) => ({
      value: String(p.id),
      label:
        `${p.name}` +
        (p.birth_date ? ` · born ${p.birth_date}` : '') +
        (p.division_ids.length ? ` · now ${p.division_ids.map(labelOf).join(', ')}` : ' · not registered'),
    }))

  const picked = (players ?? []).find((p) => String(p.id) === playerId)
  const sameSeason = (id: number) => {
    const d = divisions?.find((x) => x.id === id)
    return !!d && !!division && d.year === division.year && d.season.toLowerCase() === division.season.toLowerCase()
  }
  const kept = (picked?.division_ids ?? []).filter(sameSeason)
  const replaced = (picked?.division_ids ?? []).filter((id) => !sameSeason(id))

  const mutation = useMutation({
    mutationFn: async () => {
      await registerPlayerInDivision(Number(playerId), divisionId)
      if (position) await setRegistrationPosition(Number(playerId), divisionId, position)
    },
    onSuccess: () => {
      notifications.show({ color: 'green', message: `Registered ${picked?.name} in ${division ? divisionSeasonLabel(division) : 'this division'}.` })
      for (const key of [['players'], ['division-players'], ['draft-pool'], ['player-registrations'], ['division-requests']]) {
        queryClient.invalidateQueries({ queryKey: key })
      }
      setPlayerId(null)
      setPosition(null)
    },
    onError: (err) =>
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Could not register the player.' }),
  })

  if (readOnly) return null

  return (
    <Paper withBorder p="md" mb="md">
      <Stack gap="xs">
        <Title order={5}>Register a player</Title>
        <Text size="sm" c="dimmed">
          Add one existing player to this division — they join its draft pool until they&apos;re on a team. For someone
          new, add them on All Players first.
        </Text>
        <Group align="flex-end">
          <Select
            label="Player"
            placeholder="Search by name…"
            data={options}
            value={playerId}
            onChange={setPlayerId}
            searchable
            clearable
            flex={1}
            miw={260}
            nothingFoundMessage="No player by that name who isn't already registered here"
          />
          <Select
            label="Position (optional)"
            data={POSITIONS}
            value={position}
            onChange={setPosition}
            clearable
            w={200}
          />
          <Button disabled={!playerId} loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Register
          </Button>
        </Group>
        {picked && (kept.length > 0 || replaced.length > 0) && (
          <Alert color={replaced.length ? 'yellow' : 'blue'} p="xs">
            {kept.length > 0 && <Text size="sm">Stays registered in {kept.map(labelOf).join(', ')} too (same season).</Text>}
            {replaced.length > 0 && (
              <Text size="sm">Replaces their registration in {replaced.map(labelOf).join(', ')} (a different season).</Text>
            )}
          </Alert>
        )}
      </Stack>
    </Paper>
  )
}
