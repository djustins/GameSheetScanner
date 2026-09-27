import { useState } from 'react'
import { Alert, Button, Group, MultiSelect, Select, Stack, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  autoDraft,
  deleteDraft,
  getAutoDraftRun,
  getDraft,
  getDraftPool,
  listPicks,
  startDraft,
  submitPick,
  undoAutoDraft,
  undoLastPick,
} from '../api/draft'
import { listDivisionTeams, listDivisions } from '../api/divisions'
import { listTeamCoaches } from '../api/teams'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export function DraftPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [divisionId, setDivisionId] = useState<string | null>(null)
  const [orderPick, setOrderPick] = useState<string[]>([])
  const [pickSearch, setPickSearch] = useState('')
  const [pickPlayerId, setPickPlayerId] = useState<string | null>(null)

  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const divId = Number(divisionId)

  const { data: draft } = useQuery({
    queryKey: ['draft', divId],
    queryFn: () => getDraft(divId),
    enabled: !!divisionId,
    refetchInterval: 5000,
  })

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divId],
    queryFn: () => listDivisionTeams(divId),
    enabled: !!divisionId,
  })

  const { data: pool } = useQuery({
    queryKey: ['draft-pool', divId],
    queryFn: () => getDraftPool(divId),
    enabled: !!divisionId && !draft,
  })

  const { data: autoDraftRun } = useQuery({
    queryKey: ['auto-draft-run', divId],
    queryFn: () => getAutoDraftRun(divId),
    enabled: !!divisionId && !draft,
  })

  const { data: currentTeamCoaches } = useQuery({
    queryKey: ['team-coaches', draft?.current_team_id],
    queryFn: () => listTeamCoaches(draft!.current_team_id!),
    enabled: !!draft?.current_team_id,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const invalidateDraft = () => {
    queryClient.invalidateQueries({ queryKey: ['draft', divId] })
    queryClient.invalidateQueries({ queryKey: ['draft-pool', divId] })
    queryClient.invalidateQueries({ queryKey: ['auto-draft-run', divId] })
    queryClient.invalidateQueries({ queryKey: ['draft-picks'] })
  }

  const autoDraftMutation = useMutation({
    mutationFn: () => autoDraft(divId),
    onSuccess: (result) => {
      invalidateDraft()
      const msg = `Auto-drafted ${result.assigned} player(s) across ${result.teams} teams.`
      notifications.show({
        color: result.warnings.length ? 'yellow' : 'green',
        message: result.warnings.length ? `${msg} Warnings: ${result.warnings.join('; ')}` : msg,
      })
    },
    onError,
  })

  const undoAutoDraftMutation = useMutation({
    mutationFn: () => undoAutoDraft(divId),
    onSuccess: invalidateDraft,
    onError,
  })

  const startMutation = useMutation({
    mutationFn: () => startDraft(divId, orderPick.map(Number)),
    onSuccess: () => {
      invalidateDraft()
      setOrderPick([])
    },
    onError,
  })

  const pickMutation = useMutation({
    mutationFn: () => submitPick(draft!.id, Number(pickPlayerId)),
    onSuccess: () => {
      invalidateDraft()
      setPickPlayerId(null)
      setPickSearch('')
    },
    onError,
  })

  const undoPickMutation = useMutation({
    mutationFn: () => undoLastPick(draft!.id),
    onSuccess: invalidateDraft,
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteDraft(draft!.id),
    onSuccess: invalidateDraft,
    onError,
  })

  const { data: picks } = useQuery({
    queryKey: ['draft-picks', draft?.id],
    queryFn: () => listPicks(draft!.id),
    enabled: !!draft,
  })

  const divisionOptions = (divisions ?? []).map((d) => ({
    value: String(d.id),
    label: `${d.year} ${d.season} — ${d.age_group}`,
  }))

  const missingTeams = (teams ?? []).filter((t) => !orderPick.includes(String(t.id)))

  const canPick =
    !!draft?.current_team_id &&
    (user?.is_admin || (user?.coach_id != null && (currentTeamCoaches ?? []).some((c) => c.id === user.coach_id)))

  const poolMatches = (pool ?? []).filter((p) =>
    pickSearch.trim() ? p.name.toLowerCase().includes(pickSearch.trim().toLowerCase()) : true
  )

  return (
    <Stack>
      <Title order={2}>Draft</Title>
      <Text size="sm" c="dimmed">
        A live, snake-order draft of a division&apos;s registered-but-unrostered players onto its teams.
      </Text>

      <Select
        label="Division"
        placeholder="Choose a division"
        data={divisionOptions}
        value={divisionId}
        onChange={setDivisionId}
        searchable
      />

      {divisionId && !draft && (
        <Stack mt="md">
          <Text size="sm">{pool?.length ?? 0} player(s) currently eligible for this division&apos;s pool.</Text>

          {(teams?.length ?? 0) < 2 ? (
            <Alert color="yellow">Add at least two teams to this division before drafting.</Alert>
          ) : (
            <>
              <Group>
                <Button
                  loading={autoDraftMutation.isPending}
                  onClick={() => autoDraftMutation.mutate()}
                >
                  {autoDraftRun ? 'Re-run Auto-Draft' : 'Auto-Draft'}
                </Button>
                {autoDraftRun && (
                  <Button variant="outline" loading={undoAutoDraftMutation.isPending} onClick={() => undoAutoDraftMutation.mutate()}>
                    Undo Auto-Draft
                  </Button>
                )}
              </Group>

              {(pool?.length ?? 0) > 0 && (
                <Stack>
                  <MultiSelect
                    label="Draft order — pick teams in the order they should draft (round 1; later rounds snake back)"
                    data={(teams ?? []).map((t) => ({ value: String(t.id), label: t.name }))}
                    value={orderPick}
                    onChange={setOrderPick}
                  />
                  {missingTeams.length > 0 && (
                    <Text size="sm" c="dimmed">
                      Still need to add: {missingTeams.map((t) => t.name).join(', ')}
                    </Text>
                  )}
                  <Button
                    disabled={missingTeams.length > 0}
                    loading={startMutation.isPending}
                    onClick={() => startMutation.mutate()}
                  >
                    Start Draft
                  </Button>
                </Stack>
              )}
            </>
          )}
        </Stack>
      )}

      {draft && (
        <Stack mt="md">
          {draft.status === 'completed' ? (
            <Alert color="green">Draft complete!</Alert>
          ) : (
            <Alert color="blue">
              Round {draft.round}, Pick #{draft.current_pick_number} — it&apos;s{' '}
              <strong>{draft.current_team_name}</strong>&apos;s turn. {pool?.length ?? 0} player(s) left in the
              pool.
            </Alert>
          )}

          {draft.status !== 'completed' && canPick && (
            <Group align="flex-end">
              <TextInput
                label="Search the pool"
                value={pickSearch}
                onChange={(e) => setPickSearch(e.currentTarget.value)}
              />
              <Select
                data={poolMatches.map((p) => ({ value: String(p.id), label: p.nickname ? `${p.name} "${p.nickname}"` : p.name }))}
                value={pickPlayerId}
                onChange={setPickPlayerId}
                placeholder="Pool"
                flex={1}
              />
              <Button disabled={!pickPlayerId} loading={pickMutation.isPending} onClick={() => pickMutation.mutate()}>
                Draft
              </Button>
            </Group>
          )}
          {draft.status !== 'completed' && !canPick && (
            <Text size="sm" c="dimmed">
              Waiting for {draft.current_team_name}&apos;s coach (or an admin) to make this pick.
            </Text>
          )}

          {user?.is_admin && picks && picks.length > 0 && (
            <Button variant="subtle" onClick={() => undoPickMutation.mutate()}>
              Undo last pick
            </Button>
          )}

          <div>
            <Title order={5}>Draft order ({draft.order.length} teams)</Title>
            <Text size="sm">{draft.order.map((o) => o.team_name).join(' → ')} → (reverses each round)</Text>
          </div>

          <div>
            <Title order={5}>Pick history ({picks?.length ?? 0})</Title>
            {!picks || picks.length === 0 ? (
              <Text size="sm" c="dimmed">
                No picks yet.
              </Text>
            ) : (
              <Stack gap={2}>
                {picks.map((p, i) => (
                  <Text size="sm" key={i}>
                    #{p.pick_number} (Rd {p.round}) — {p.team_name}: {p.player_name}
                    {p.nickname ? ` "${p.nickname}"` : ''}
                  </Text>
                ))}
              </Stack>
            )}
          </div>

          {user?.is_admin && (
            <Button
              color="red"
              variant="outline"
              onClick={() =>
                confirm('Delete this draft’s order and pick history? Already-drafted players stay on their roster.') &&
                deleteMutation.mutate()
              }
            >
              Delete this draft
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  )
}
