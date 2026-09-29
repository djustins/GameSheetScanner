import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Button, Group, Stack, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listDivisionTeams } from '../api/divisions'
import { TeamRosterView } from '../components/TeamRosterView'

export function TeamRosterPage() {
  const { teamId: teamIdParam } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const teamId = Number(teamIdParam)
  const divisionId = Number(searchParams.get('division'))

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId),
    enabled: !!divisionId,
  })
  const team = teams?.find((t) => t.id === teamId)

  if (!divisionId) {
    return <Text c="red">Missing division context — go back to Divisions and open this team from there.</Text>
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>{team?.name ?? `Team #${teamId}`}</Title>
        <Button variant="subtle" onClick={() => navigate(-1)}>
          Back
        </Button>
      </Group>
      <TeamRosterView teamId={teamId} divisionId={divisionId} />
    </Stack>
  )
}
