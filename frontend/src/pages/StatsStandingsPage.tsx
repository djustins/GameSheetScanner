import { useState } from 'react'
import { Select, Stack, Table, Tabs, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getPlayerStats, getStandings, listDivisions } from '../api/divisions'
import { divisionSeasonLabel } from '../utils/format'

export function StatsStandingsPage() {
  const [divisionId, setDivisionId] = useState<string | null>(null)

  const { data: divisions, isLoading } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })

  const { data: standings } = useQuery({
    queryKey: ['standings', divisionId],
    queryFn: () => getStandings(Number(divisionId)),
    enabled: divisionId != null,
  })

  const { data: stats } = useQuery({
    queryKey: ['stats', divisionId],
    queryFn: () => getPlayerStats(Number(divisionId)),
    enabled: divisionId != null,
  })

  const divisionOptions = (divisions ?? []).map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))

  return (
    <Stack>
      <Title order={2}>Stats & Standings</Title>
      <Select
        label="Division"
        placeholder="Choose a division"
        data={divisionOptions}
        value={divisionId}
        onChange={setDivisionId}
        disabled={isLoading}
        searchable
        clearable
      />

      {divisionId && (
        <Tabs defaultValue="standings">
          <Tabs.List>
            <Tabs.Tab value="standings">Standings</Tabs.Tab>
            <Tabs.Tab value="stats">Player Stats</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="standings" pt="md">
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>Team</Table.Th>
                  <Table.Th>GP</Table.Th>
                  <Table.Th>W</Table.Th>
                  <Table.Th>L</Table.Th>
                  <Table.Th>OTW</Table.Th>
                  <Table.Th>OTL</Table.Th>
                  <Table.Th>T</Table.Th>
                  <Table.Th>PTS</Table.Th>
                  <Table.Th>GF</Table.Th>
                  <Table.Th>GA</Table.Th>
                  <Table.Th>DIFF</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(standings ?? []).map((s, i) => (
                  <Table.Tr key={s.team}>
                    <Table.Td>{i + 1}</Table.Td>
                    <Table.Td>{s.team}</Table.Td>
                    <Table.Td>{s.games_played}</Table.Td>
                    <Table.Td>{s.wins}</Table.Td>
                    <Table.Td>{s.losses}</Table.Td>
                    <Table.Td>{s.ot_wins}</Table.Td>
                    <Table.Td>{s.ot_losses}</Table.Td>
                    <Table.Td>{s.ties}</Table.Td>
                    <Table.Td>{s.points}</Table.Td>
                    <Table.Td>{s.goals_for}</Table.Td>
                    <Table.Td>{s.goals_against}</Table.Td>
                    <Table.Td>{s.goal_diff}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {standings?.length === 0 && (
              <Text c="dimmed" size="sm" mt="sm">
                No completed games yet.
              </Text>
            )}
          </Tabs.Panel>

          <Tabs.Panel value="stats" pt="md">
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Player</Table.Th>
                  <Table.Th>Team</Table.Th>
                  <Table.Th>#</Table.Th>
                  <Table.Th>G</Table.Th>
                  <Table.Th>A</Table.Th>
                  <Table.Th>PTS</Table.Th>
                  <Table.Th>PIM</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(stats ?? []).map((s, i) => (
                  <Table.Tr key={i}>
                    <Table.Td>{s.name}</Table.Td>
                    <Table.Td>{s.team}</Table.Td>
                    <Table.Td>{s.number}</Table.Td>
                    <Table.Td>{s.goals}</Table.Td>
                    <Table.Td>{s.assists}</Table.Td>
                    <Table.Td>{s.points}</Table.Td>
                    <Table.Td>{s.penalties}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {stats?.length === 0 && (
              <Text c="dimmed" size="sm" mt="sm">
                No stats recorded yet.
              </Text>
            )}
          </Tabs.Panel>
        </Tabs>
      )}
    </Stack>
  )
}
