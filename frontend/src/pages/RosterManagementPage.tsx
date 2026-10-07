import { useState } from 'react'
import { Alert, Button, Group, SegmentedControl, Stack, Tabs, Text, Title } from '@mantine/core'
import { useSearchParams } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  autoDraft,
  getAutoDraftRun,
  getDraft,
  getDraftPool,
  storeAutoDraft,
  undoAutoDraft,
} from '../api/draft'
import { listDivisionTeams } from '../api/divisions'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ReadOnlyNotice, useAccess, Writable } from '../auth/access'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { AutoDraftResults, DraftNotes } from '../components/AutoDraftResults'
import { DraftRoom } from '../components/DraftRoom'
import { DraftSetup } from '../components/DraftSetup'
import { MoveHistory } from '../components/MoveHistory'
import { TradePanel } from '../components/TradePanel'
import { UnplacedPlayers } from '../components/UnplacedPlayers'
import type { Team } from '../api/types'
import { RequestsPage } from './RequestsPage'

/** Everything that decides who is on which team in the Working Division: the
 * draft, the play-with requests that feed Auto-Draft, trades (with the move
 * history, for admins), and the players not on a team yet.
 * Each section shows only to roles with its page; ?tab=requests (or trades)
 * opens that section directly. */
export function RosterManagementPage() {
  const { canView } = useAccess()
  const { user } = useAuth()
  const { workingDivisionId } = useWorkingDivision()
  const [params, setParams] = useSearchParams()
  const tabs = [
    canView('draft') && { value: 'draft', label: 'Draft' },
    canView('teams') && { value: 'requests', label: 'Requests' },
    // Trades used to sit on both the Draft and the Team Rosters pages.
    (canView('draft') || canView('rosters')) && { value: 'trades', label: 'Trades' },
    (canView('draft') || canView('rosters')) && { value: 'unplaced', label: 'Unplaced Players' },
  ].filter((t) => !!t)
  const tab = tabs.find((t) => t.value === params.get('tab'))?.value ?? tabs[0]?.value

  return (
    <Stack>
      <Title order={2}>Roster Management</Title>
      {/* keepMounted off so the draft's polling stops while another section is open. */}
      <Tabs
        keepMounted={false}
        value={tab}
        onChange={(next) => setParams(next && next !== tabs[0]?.value ? { tab: next } : {})}
      >
        <Tabs.List mb="md">
          {tabs.map((t) => (
            <Tabs.Tab key={t.value} value={t.value}>
              {t.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        <Tabs.Panel value="draft">
          <DraftBoard />
        </Tabs.Panel>
        <Tabs.Panel value="requests">
          <RequestsPage />
        </Tabs.Panel>
        <Tabs.Panel value="trades">
          <Stack>
            <Text size="sm" c="dimmed">
              Swap players between two teams in one step, with a before-and-after look at both teams and every
              request or sibling pair the trade joins or splits.
            </Text>
            {workingDivisionId == null ? (
              <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
            ) : (
              <>
                <TradePanel divisionId={workingDivisionId} open />
                {user?.is_admin && <MoveHistory divisionId={workingDivisionId} />}
              </>
            )}
          </Stack>
        </Tabs.Panel>
        <Tabs.Panel value="unplaced">
          {workingDivisionId == null ? (
            <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
          ) : (
            <UnplacedPlayers divisionId={workingDivisionId} />
          )}
        </Tabs.Panel>
      </Tabs>
    </Stack>
  )
}

function DraftBoard() {
  const { workingDivisionId } = useWorkingDivision()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'live' | 'mock'>('live')
  // Kept on the page (not just a toast) since warnings -- e.g. a skipped
  // play-with request -- can be several and worth reading carefully.
  const [autoDraftResult, setAutoDraftResult] = useState<{ divisionId: number; message: string; warnings: string[] } | null>(null)

  const divId = workingDivisionId ?? 0

  const { data: draft } = useQuery({
    queryKey: ['draft', divId],
    queryFn: () => getDraft(divId),
    enabled: workingDivisionId != null,
    refetchInterval: 5000,
  })

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divId],
    queryFn: () => listDivisionTeams(divId),
    enabled: workingDivisionId != null,
  })

  const { data: pool } = useQuery({
    queryKey: ['draft-pool', divId],
    queryFn: () => getDraftPool(divId),
    enabled: workingDivisionId != null && !draft,
  })

  const { data: autoDraftRun } = useQuery({
    queryKey: ['auto-draft-run', divId],
    queryFn: () => getAutoDraftRun(divId),
    enabled: workingDivisionId != null && !draft,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const invalidateDraft = () => {
    queryClient.invalidateQueries({ queryKey: ['draft', divId] })
    queryClient.invalidateQueries({ queryKey: ['draft-pool', divId] })
    queryClient.invalidateQueries({ queryKey: ['draft-pool-for'] })
    queryClient.invalidateQueries({ queryKey: ['auto-draft-run', divId] })
    queryClient.invalidateQueries({ queryKey: ['auto-draft-results', divId] })
    queryClient.invalidateQueries({ queryKey: ['roster'] })
    queryClient.invalidateQueries({ queryKey: ['draft-picks'] })
  }

  const autoDraftMutation = useMutation({
    mutationFn: () => autoDraft(divId),
    onSuccess: (result) => {
      invalidateDraft()
      setAutoDraftResult({
        divisionId: divId,
        message: `Auto-drafted ${result.assigned} player(s) across ${result.teams} teams.`,
        warnings: result.warnings,
      })
    },
    onError,
  })

  const undoAutoDraftMutation = useMutation({
    mutationFn: () => undoAutoDraft(divId),
    onSuccess: invalidateDraft,
    onError,
  })

  const storeAutoDraftMutation = useMutation({
    mutationFn: () => storeAutoDraft(divId),
    onSuccess: () => {
      invalidateDraft()
      notifications.show({ color: 'green', message: 'Auto-draft stored.' })
    },
    onError,
  })

  return (
    <Stack>
      <Title order={3}>Draft</Title>
      <Text size="sm" c="dimmed">
        A live, snake-order draft of a division&apos;s registered-but-unrostered players onto its teams.
      </Text>
      <ReadOnlyNotice />

      {workingDivisionId == null && <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>}

      {workingDivisionId != null && (
        <SegmentedControl
          w="fit-content"
          mx="auto"
          value={mode}
          onChange={(v) => setMode(v as 'live' | 'mock')}
          data={[
            { value: 'live', label: 'Live draft' },
            { value: 'mock', label: 'Mock draft' },
          ]}
        />
      )}

      {workingDivisionId != null && mode === 'mock' && (
        <MockDraft divisionId={divId} teams={teams ?? []} onChanged={invalidateDraft} />
      )}

      {mode === 'live' && autoDraftResult && autoDraftResult.divisionId === divId && (
        <Alert
          color={autoDraftResult.warnings.length ? 'yellow' : 'green'}
          title={autoDraftResult.message}
          withCloseButton
          onClose={() => setAutoDraftResult(null)}
        >
          {autoDraftResult.warnings.length > 0 && (
            <Stack gap={2}>
              {autoDraftResult.warnings.map((w, i) => (
                <Text size="sm" key={i}>
                  • {w}
                </Text>
              ))}
            </Stack>
          )}
        </Alert>
      )}

      {mode === 'live' && workingDivisionId != null && !draft && (
        <Writable>
        <Stack mt="md">
          <Text size="sm">{pool?.length ?? 0} player(s) currently eligible for this division&apos;s pool.</Text>

          {(teams?.length ?? 0) < 2 ? (
            <Alert color="yellow">Add at least two teams to this division before drafting.</Alert>
          ) : (
            <>
              {autoDraftRun?.stored ? (
                // Stored: no Re-run or Undo, so the teams can't change by accident.
                <Alert color="green">
                  Auto-draft stored — the teams are locked in. Use a trade below to move a player.
                </Alert>
              ) : (
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
                  {autoDraftRun && (
                    <Button
                      color="green"
                      loading={storeAutoDraftMutation.isPending}
                      onClick={() =>
                        confirm('Store this auto-draft? Re-run and Undo go away for good — after this, teams change only by trades and roster edits.') &&
                        storeAutoDraftMutation.mutate()
                      }
                    >
                      Store Auto-Draft
                    </Button>
                  )}
                </Group>
              )}

              {(pool?.length ?? 0) > 0 && (
                <DraftSetup divisionId={divId} teams={teams ?? []} mock={false} onStarted={invalidateDraft} />
              )}
            </>
          )}
        </Stack>
        </Writable>
      )}

      {mode === 'live' && draft && workingDivisionId != null && (
        <DraftRoom draft={draft} divisionId={divId} teams={teams ?? []} onChanged={invalidateDraft} />
      )}

      {mode === 'live' && workingDivisionId != null && (
        <Stack mt="lg" gap="lg">
          <AutoDraftResults divisionId={workingDivisionId} />
          <DraftNotes divisionId={workingDivisionId} />
        </Stack>
      )}
    </Stack>
  )
}

/** A practice draft for the division: same room, same rules, but its picks are
 * never saved to a roster, so it can be run and thrown away any number of times. */
function MockDraft({ divisionId, teams, onChanged }: { divisionId: number; teams: Team[]; onChanged: () => void }) {
  const { readOnly } = useAccess()
  const { data: draft, isLoading } = useQuery({
    queryKey: ['draft', divisionId, 'mock'],
    queryFn: () => getDraft(divisionId, true),
    refetchInterval: 5000,
  })
  if (isLoading) return null
  if (draft) return <DraftRoom draft={draft} divisionId={divisionId} teams={teams} onChanged={onChanged} />
  if (teams.length < 2) return <Alert color="yellow">Add at least two teams to this division before drafting.</Alert>
  return (
    <Stack>
      <Alert color="blue" title="Mock draft">
        A practice run for testing settings or training coaches. Nothing here is saved to a roster, and everyone
        registered in the division is in the pool, even players already on a team. End it and start over as often as
        you like; it runs alongside the real draft without touching it.
      </Alert>
      {readOnly ? (
        <Text size="sm" c="dimmed">
          No mock draft is running.
        </Text>
      ) : (
        <DraftSetup divisionId={divisionId} teams={teams} mock onStarted={onChanged} />
      )}
    </Stack>
  )
}
