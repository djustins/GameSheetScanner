import { useState } from 'react'
import { Alert, Select, Stack, Tabs, Title } from '@mantine/core'
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
  const [teamId, setTeamId] = useState<string | null>(null)

  const { data: teams } = useQuery({
    queryKey: ['division-teams', workingDivisionId],
    queryFn: () => listDivisionTeams(workingDivisionId!),
    enabled: workingDivisionId != null,
  })

  const coachQueries = useQueries({
    queries: (teams ?? []).map((t) => ({ queryKey: ['team-coaches', t.id], queryFn: () => listTeamCoaches(t.id) })),
  })
  // "Team (Coach)", like the Streamlit app's team pickers.
  const teamLabel = (index: number, name: string) => {
    const coaches = coachQueries[index]?.data ?? []
    return `${name} (${coaches.length ? coaches.map((c) => c.name).join(', ') : 'no coach'})`
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
              <Select
                label="Select a team"
                placeholder={teams && teams.length === 0 ? 'No teams in this division yet' : 'Choose a team'}
                data={(teams ?? []).map((t, i) => ({ value: String(t.id), label: teamLabel(i, t.name) }))}
                value={teamId}
                onChange={setTeamId}
                disabled={!teams || teams.length === 0}
                searchable
              />
              {teamId && <TeamRosterView teamId={Number(teamId)} divisionId={workingDivisionId} />}
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
