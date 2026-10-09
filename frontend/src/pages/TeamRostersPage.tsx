import { useState } from 'react'
import { Alert, Stack, Tabs, Text, Title } from '@mantine/core'
import { useQueries, useQuery } from '@tanstack/react-query'
import { listDivisionTeams } from '../api/divisions'
import { listTeamCoaches } from '../api/teams'
import { DivisionPlayersTable } from '../components/DivisionPlayersTable'
import { TeamRosterView } from '../components/TeamRosterView'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { useAccess } from '../auth/access'

export function TeamRostersPage() {
  const { workingDivisionId } = useWorkingDivision()
  const { canView } = useAccess()
  // Streamlit gates these separately: Team Rosters is 'rosters', the
  // division-wide Players list is 'teams' (Teams → Players).
  const canRosters = canView('rosters')
  const canPlayers = canView('teams')
  const [pickedTeamId, setPickedTeamId] = useState<string | null>(null)

  const { data: unsortedTeams } = useQuery({
    queryKey: ['division-teams', workingDivisionId],
    queryFn: () => listDivisionTeams(workingDivisionId!),
    enabled: workingDivisionId != null,
  })
  // A tab per team in alphabetical order; the first one shows until another is picked.
  const teams = unsortedTeams && [...unsortedTeams].sort((a, b) => a.name.localeCompare(b.name))
  const teamId = teams?.some((t) => String(t.id) === pickedTeamId) ? pickedTeamId : teams?.[0] ? String(teams[0].id) : null

  const coachQueries = useQueries({
    queries: (teams ?? []).map((t) => ({ queryKey: ['team-coaches', t.id], queryFn: () => listTeamCoaches(t.id) })),
  })
  const coachesOf = (id: string | null) => {
    const coaches = coachQueries[(teams ?? []).findIndex((t) => String(t.id) === id)]?.data ?? []
    return coaches.length ? coaches.map((c) => c.name).join(', ') : 'No coach assigned'
  }

  if (workingDivisionId == null) {
    return (
      <Stack>
        <Title order={2}>Team Rosters</Title>
        <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
      </Stack>
    )
  }

  return (
    <Stack>
      <Title order={2}>Team Rosters</Title>
      <Tabs defaultValue={canRosters ? 'rosters' : 'players'}>
        <Tabs.List>
          {canRosters && <Tabs.Tab value="rosters">Rosters</Tabs.Tab>}
          {canPlayers && <Tabs.Tab value="players">Players</Tabs.Tab>}
        </Tabs.List>

        {canRosters && (
          <Tabs.Panel value="rosters" pt="md">
            <Stack>
              {teams && teams.length === 0 && <Alert color="blue">No teams in this division yet.</Alert>}
              {teamId && (
                <>
                  <Tabs variant="pills" value={teamId} onChange={setPickedTeamId}>
                    <Tabs.List>
                      {teams!.map((t) => (
                        <Tabs.Tab key={t.id} value={String(t.id)}>
                          {t.name}
                        </Tabs.Tab>
                      ))}
                    </Tabs.List>
                  </Tabs>
                  <Text size="sm" c="dimmed" ta="left">
                    Coach: {coachesOf(teamId)}
                  </Text>
                  <TeamRosterView key={teamId} teamId={Number(teamId)} divisionId={workingDivisionId} />
                </>
              )}
            </Stack>
          </Tabs.Panel>
        )}

        {canPlayers && (
          <Tabs.Panel value="players" pt="md">
            <DivisionPlayersTable divisionId={workingDivisionId} />
          </Tabs.Panel>
        )}
      </Tabs>
    </Stack>
  )
}
