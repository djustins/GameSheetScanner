import { useState } from 'react'
import { Accordion, Alert, Select, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getDivisionEvaluations, listDivisions } from '../api/divisions'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { divisionSeasonLabel } from '../utils/format'
import { GRADE_TIERS } from '../utils/grades'

/** Only the evaluations recorded for the Working Division -- no grades carried
 * over from other seasons -- with a team filter, a grade breakdown, and who
 * hasn't been evaluated yet. Mirrors the Streamlit app's Teams → Evals tab. */
export function EvalsPage() {
  const { workingDivisionId } = useWorkingDivision()
  const [teamFilter, setTeamFilter] = useState<string | null>('All Teams')
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data } = useQuery({
    queryKey: ['division-evaluations', workingDivisionId],
    queryFn: () => getDivisionEvaluations(workingDivisionId!),
    enabled: workingDivisionId != null,
  })

  if (workingDivisionId == null) {
    return (
      <Stack>
        <Title order={2}>Evals</Title>
        <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
      </Stack>
    )
  }

  const division = divisions?.find((d) => d.id === workingDivisionId)
  const evaluations = data?.evaluations ?? []
  const teamNames = [...new Set(evaluations.map((e) => e.team_name).filter((t): t is string => !!t))].sort()
  const hasNoTeam = evaluations.some((e) => !e.team_name)
  const shown = evaluations.filter(
    (e) => teamFilter === 'All Teams' || (e.team_name ?? 'No team') === teamFilter
  )
  const tiers = shown.map((e) => (e.grade ?? '').trim().toUpperCase()).filter((g) => GRADE_TIERS.includes(g))

  return (
    <Stack>
      <Title order={2}>Evals{division ? ` — ${divisionSeasonLabel(division)}` : ''}</Title>
      <Text size="sm" c="dimmed" maw={760}>
        Only the evaluations recorded for the Working Division — no grades carried over from other seasons. Change the
        Working Division in the sidebar to see another season&apos;s.
      </Text>

      <Select
        label="Team"
        data={['All Teams', ...teamNames, ...(hasNoTeam ? ['No team'] : [])]}
        value={teamFilter}
        onChange={setTeamFilter}
        allowDeselect={false}
        w={260}
      />

      {shown.length === 0 ? (
        <Text size="sm">No evaluations recorded for this season yet.</Text>
      ) : (
        <>
          <Text size="sm" c="dimmed">
            {shown.length} evaluation(s) · {new Set(shown.map((e) => e.player_id)).size} player(s)
            {tiers.length > 0 &&
              ' · ' +
                GRADE_TIERS.filter((g) => tiers.includes(g))
                  .map((g) => `${g}: ${tiers.filter((t) => t === g).length}`)
                  .join(', ')}
          </Text>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Team</Table.Th>
                <Table.Th>#</Table.Th>
                <Table.Th>Player</Table.Th>
                <Table.Th>Grade</Table.Th>
                <Table.Th>Entered</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{e.team_name ?? '—'}</Table.Td>
                  <Table.Td>{e.number ?? '—'}</Table.Td>
                  <Table.Td>{e.name}</Table.Td>
                  <Table.Td>{e.grade}</Table.Td>
                  <Table.Td>{(e.created_at ?? '').slice(0, 10)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}

      {teamFilter === 'All Teams' && (data?.not_evaluated.length ?? 0) > 0 && (
        <Accordion variant="contained">
          <Accordion.Item value="ungraded">
            <Accordion.Control>Not yet evaluated this season ({data!.not_evaluated.length})</Accordion.Control>
            <Accordion.Panel>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Player</Table.Th>
                    <Table.Th>Team(s)</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data!.not_evaluated.map((p) => (
                    <Table.Tr key={p.player_id}>
                      <Table.Td>{p.name}</Table.Td>
                      <Table.Td>{p.teams.join(', ') || '—'}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Stack>
  )
}
