import { useMemo, useState } from 'react'
import { Button, Group, Select, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getPreviousDivision, listDivisionTeams } from '../api/divisions'
import { assignCoach, listTeamCoaches, removeCoach } from '../api/teams'
import { getCoachChildrenInDivision, linkCoachChild } from '../api/coaches'
import type { Coach, DivisionPlayer, Team } from '../api/types'
import { ApiError } from '../api/client'
import { coachLabel, divisionSeasonLabel } from '../utils/format'

async function coachesByTeam(teams: Team[]): Promise<Record<number, Coach[]>> {
  const entries = await Promise.all(teams.map(async (t) => [t.id, await listTeamCoaches(t.id)] as const))
  return Object.fromEntries(entries)
}

function ChildLinkSearch({ coach, divisionPlayers, divisionId }: { coach: Coach; divisionPlayers: DivisionPlayer[]; divisionId: number }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [pickId, setPickId] = useState<string | null>(null)

  const linkMutation = useMutation({
    mutationFn: () => linkCoachChild(coach.id, Number(pickId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['coach-children-in-division', coach.id, divisionId] })
      setPickId(null)
      setSearch('')
    },
  })

  const matches = divisionPlayers.filter((p) => !search.trim() || p.name.toLowerCase().includes(search.trim().toLowerCase()))

  return (
    <Group gap="xs" mt={4}>
      <TextInput
        size="xs"
        placeholder="Search their child by name"
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
      />
      <Select
        size="xs"
        placeholder="Player"
        data={matches.map((p) => ({ value: String(p.id), label: p.name }))}
        value={pickId}
        onChange={setPickId}
      />
      <Button size="xs" disabled={!pickId} onClick={() => linkMutation.mutate()}>
        Link as child
      </Button>
    </Group>
  )
}

export function CoachCarryoverPanel({
  divisionId,
  teams,
  divisionPlayers,
}: {
  divisionId: number
  teams: Team[]
  divisionPlayers: DivisionPlayer[]
}) {
  const queryClient = useQueryClient()
  const [pendingPicks, setPendingPicks] = useState<Record<number, number>>({})

  const { data: previousDivision } = useQuery({
    queryKey: ['previous-division', divisionId],
    queryFn: () => getPreviousDivision(divisionId),
  })

  const { data: previousTeams } = useQuery({
    queryKey: ['division-teams', previousDivision?.id],
    queryFn: () => listDivisionTeams(previousDivision!.id),
    enabled: !!previousDivision,
  })

  const { data: previousCoachesByTeam } = useQuery({
    queryKey: ['coaches-by-team', previousDivision?.id],
    queryFn: () => coachesByTeam(previousTeams!),
    enabled: !!previousDivision && !!previousTeams,
  })

  const { data: currentCoachesByTeam } = useQuery({
    queryKey: ['coaches-by-team', divisionId],
    queryFn: () => coachesByTeam(teams),
    enabled: teams.length > 0,
  })

  const carryRows = useMemo(() => {
    if (!previousTeams || !previousCoachesByTeam) return []
    return previousTeams.flatMap((pt) => (previousCoachesByTeam[pt.id] ?? []).map((coach) => ({ previousTeam: pt, coach })))
  }, [previousTeams, previousCoachesByTeam])

  const currentTeamByCoachId = useMemo(() => {
    const map: Record<number, number> = {}
    for (const [teamId, coaches] of Object.entries(currentCoachesByTeam ?? {})) {
      for (const c of coaches) map[c.id] = Number(teamId)
    }
    return map
  }, [currentCoachesByTeam])

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const assignAllMutation = useMutation({
    mutationFn: async () => {
      for (const [coachIdStr, teamId] of Object.entries(pendingPicks)) {
        const coachId = Number(coachIdStr)
        const priorTeamId = currentTeamByCoachId[coachId]
        if (priorTeamId != null && priorTeamId !== teamId) {
          await removeCoach(priorTeamId, coachId)
        }
        await assignCoach(teamId, coachId)
      }
    },
    onSuccess: () => {
      setPendingPicks({})
      queryClient.invalidateQueries({ queryKey: ['coaches-by-team', divisionId] })
      queryClient.invalidateQueries({ queryKey: ['team-coaches'] })
    },
    onError,
  })

  const removeMutation = useMutation({
    mutationFn: ({ teamId, coachId }: { teamId: number; coachId: number }) => removeCoach(teamId, coachId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['coaches-by-team', divisionId] }),
    onError,
  })

  if (!previousDivision) {
    return (
      <Text size="sm" c="dimmed">
        No earlier division found for this age group yet.
      </Text>
    )
  }
  if (teams.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Add a team above first, then a coach can be assigned to it.
      </Text>
    )
  }
  if (carryRows.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No coaches were assigned to a team in {divisionSeasonLabel(previousDivision)}.
      </Text>
    )
  }

  const claimedTeamByTeamId: Record<number, number> = {}
  for (const [coachId, teamId] of Object.entries(currentTeamByCoachId)) {
    claimedTeamByTeamId[teamId] = Number(coachId)
  }
  for (const [coachIdStr, teamId] of Object.entries(pendingPicks)) {
    claimedTeamByTeamId[teamId] = Number(coachIdStr)
  }

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Coaches from {divisionSeasonLabel(previousDivision)} — team names aren&apos;t carried over, so pick this
        season&apos;s team for each.
      </Text>
      {carryRows.map(({ previousTeam, coach }) => {
        const currentTeamId = currentTeamByCoachId[coach.id]
        const selectableTeams = teams.filter((t) => claimedTeamByTeamId[t.id] === undefined || claimedTeamByTeamId[t.id] === coach.id)
        return (
          <CarryoverRow
            key={coach.id}
            coach={coach}
            previousTeamName={previousTeam.name}
            divisionId={divisionId}
            divisionPlayers={divisionPlayers}
            selectableTeams={selectableTeams}
            pickedTeamId={pendingPicks[coach.id] ?? currentTeamId ?? null}
            onPick={(teamId) => setPendingPicks((prev) => ({ ...prev, [coach.id]: teamId }))}
            onRemove={currentTeamId != null ? () => removeMutation.mutate({ teamId: currentTeamId, coachId: coach.id }) : undefined}
          />
        )
      })}
      <Group>
        <Text size="sm" c="dimmed">
          {Object.keys(pendingPicks).length === 0
            ? 'Pick a team above for at least one coach to assign them.'
            : `${Object.keys(pendingPicks).length} coach(es) ready to assign.`}
        </Text>
        <Button
          size="xs"
          disabled={Object.keys(pendingPicks).length === 0}
          loading={assignAllMutation.isPending}
          onClick={() => assignAllMutation.mutate()}
        >
          Assign All
        </Button>
      </Group>
    </Stack>
  )
}

function CarryoverRow({
  coach,
  previousTeamName,
  divisionId,
  divisionPlayers,
  selectableTeams,
  pickedTeamId,
  onPick,
  onRemove,
}: {
  coach: Coach
  previousTeamName: string
  divisionId: number
  divisionPlayers: DivisionPlayer[]
  selectableTeams: Team[]
  pickedTeamId: number | null
  onPick: (teamId: number) => void
  onRemove?: () => void
}) {
  const { data: children } = useQuery({
    queryKey: ['coach-children-in-division', coach.id, divisionId],
    queryFn: () => getCoachChildrenInDivision(coach.id, divisionId),
  })
  const isRelevant = !!children && children.length > 0

  return (
    <Group align="flex-start" justify="space-between">
      <div>
        <Text size="sm" fw={isRelevant ? 500 : undefined} c={isRelevant ? undefined : 'dimmed'}>
          {coachLabel(coach)} — was {previousTeamName}
          {!isRelevant && ' (no player registered in this division)'}
        </Text>
        {!isRelevant && (
          <ChildLinkSearch coach={coach} divisionPlayers={divisionPlayers} divisionId={divisionId} />
        )}
      </div>
      <Group gap="xs">
        <Select
          size="xs"
          placeholder="— Select a team —"
          data={selectableTeams.map((t) => ({ value: String(t.id), label: t.name }))}
          value={pickedTeamId != null ? String(pickedTeamId) : null}
          onChange={(v) => v && onPick(Number(v))}
          disabled={!isRelevant}
        />
        {onRemove && (
          <Button size="xs" variant="subtle" color="red" onClick={onRemove}>
            Remove
          </Button>
        )}
      </Group>
    </Group>
  )
}
