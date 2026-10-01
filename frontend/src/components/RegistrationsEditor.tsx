import { Group, MultiSelect, Select, Stack } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import {
  listPlayerRegistrations,
  registerPlayerInDivision,
  setRegistrationPosition,
  unregisterPlayerFromDivision,
} from '../api/players'
import { ApiError } from '../api/client'
import { useAccess } from '../auth/access'
import { divisionSeasonLabel } from '../utils/format'

const POSITIONS = ['', 'Forward', 'Defense', 'Forward or Defense', 'Goalie']

/** A player's division registrations beyond the main one -- another age group
 * they also play in this season -- and the position they registered with in
 * each (per division: a goalie in one age group can skate in another). */
export function RegistrationsEditor({ playerId, mainDivisionId }: { playerId: number; mainDivisionId: number | null }) {
  const { readOnly } = useAccess()
  const queryClient = useQueryClient()
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data: registrations } = useQuery({
    queryKey: ['player-registrations', playerId, mainDivisionId],
    queryFn: () => listPlayerRegistrations(playerId),
  })

  const refresh = () => {
    for (const key of [['player-registrations', playerId], ['players'], ['division-players'], ['draft-pool'], ['position']]) {
      queryClient.invalidateQueries({ queryKey: key })
    }
  }
  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const registerMutation = useMutation({
    mutationFn: ({ divisionId, add }: { divisionId: number; add: boolean }) =>
      add ? registerPlayerInDivision(playerId, divisionId) : unregisterPlayerFromDivision(playerId, divisionId),
    onSuccess: refresh,
    onError,
  })
  const positionMutation = useMutation({
    mutationFn: ({ divisionId, position }: { divisionId: number; position: string }) =>
      setRegistrationPosition(playerId, divisionId, position || null),
    onSuccess: refresh,
    onError,
  })

  if (mainDivisionId == null || !divisions) return null
  const main = divisions.find((d) => d.id === mainDivisionId)
  const sameSeason = divisions.filter(
    (d) => d.id !== mainDivisionId && main && d.year === main.year && d.season.toLowerCase() === main.season.toLowerCase()
  )
  const others = (registrations ?? []).filter((r) => r.division_id !== mainDivisionId).map((r) => String(r.division_id))
  const label = (id: number) => {
    const d = divisions.find((x) => x.id === id)
    return d ? divisionSeasonLabel(d) : String(id)
  }

  return (
    <Stack gap="xs" mt="xs">
      <MultiSelect
        label="Also registered in"
        description="Another age group this player also plays in this season."
        placeholder={sameSeason.length ? 'None' : 'No other divisions this season'}
        data={sameSeason.map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))}
        value={others}
        onChange={(next) => {
          const added = next.find((v) => !others.includes(v))
          const removed = others.find((v) => !next.includes(v))
          if (added) registerMutation.mutate({ divisionId: Number(added), add: true })
          if (removed) registerMutation.mutate({ divisionId: Number(removed), add: false })
        }}
        disabled={readOnly || !sameSeason.length}
      />
      <Group grow>
        {(registrations ?? []).map((r) => (
          <Select
            key={r.division_id}
            label={`Registered position — ${label(r.division_id)}`}
            data={[...new Set([...POSITIONS, r.position ?? ''])].map((p) => ({ value: p, label: p || '(not given)' }))}
            value={r.position ?? ''}
            onChange={(v) => v != null && positionMutation.mutate({ divisionId: r.division_id, position: v })}
            allowDeselect={false}
            disabled={readOnly}
          />
        ))}
      </Group>
    </Stack>
  )
}
