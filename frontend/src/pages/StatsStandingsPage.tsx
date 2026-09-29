import { useMemo, useState } from 'react'
import { Alert, Select, Stack, Table, Tabs, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getPlayerStats, getStandings } from '../api/divisions'
import { listPlayers } from '../api/players'
import type { Player } from '../api/types'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { useAccess } from '../auth/access'

export function StatsStandingsPage() {
  const { workingDivisionId } = useWorkingDivision()
  const { canView } = useAccess()
  const canStandings = canView('standings')
  const canStats = canView('stats')

  const { data: standings } = useQuery({
    queryKey: ['standings', workingDivisionId],
    queryFn: () => getStandings(workingDivisionId!),
    enabled: workingDivisionId != null && canStandings,
  })

  const { data: stats } = useQuery({
    queryKey: ['stats', workingDivisionId],
    queryFn: () => getPlayerStats(workingDivisionId!),
    enabled: workingDivisionId != null && canStats,
  })

  const { data: players } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers(), enabled: canStats })
  const playersById = useMemo(() => new Map((players ?? []).map((p) => [p.id, p])), [players])
  const [teamFilter, setTeamFilter] = useState('All Teams')
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)
  const teamNames = useMemo(() => [...new Set((stats ?? []).map((s) => s.team))].sort(), [stats])
  const filteredStats = (stats ?? []).filter((s) => teamFilter === 'All Teams' || s.team === teamFilter)

  return (
    <Stack>
      <Title order={2}>Stats & Standings</Title>

      {workingDivisionId == null && <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>}

      {workingDivisionId != null && (
        <Tabs defaultValue={canStandings ? 'standings' : 'stats'}>
          <Tabs.List>
            {canStandings && <Tabs.Tab value="standings">Standings</Tabs.Tab>}
            {canStats && <Tabs.Tab value="stats">Player Stats</Tabs.Tab>}
          </Tabs.List>

          <Tabs.Panel value="standings" pt="md">
            <Text size="xs" c="dimmed" mb="sm">
              3 pts for a regulation win, 2 for an OT/shootout win, 1 for an OT/shootout loss, 0 for a regulation
              loss. Ties broken by: head-to-head record, goal differential, regulation wins, OT wins, goals against,
              goals for.
            </Text>
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
            <Select
              label="Filter by team"
              data={['All Teams', ...teamNames]}
              value={teamFilter}
              onChange={(v) => setTeamFilter(v ?? 'All Teams')}
              w={260}
              mb="sm"
            />
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Team</Table.Th>
                  <Table.Th>#</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>G</Table.Th>
                  <Table.Th>A</Table.Th>
                  <Table.Th>PTS</Table.Th>
                  <Table.Th>PIM</Table.Th>
                  <Table.Th>SO Made</Table.Th>
                  <Table.Th>SO Missed</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filteredStats.map((s, i) => {
                  const player = s.player_id != null ? playersById.get(s.player_id) : undefined
                  return (
                    <Table.Tr
                      key={i}
                      style={player ? { cursor: 'pointer' } : undefined}
                      onClick={player ? () => setSelectedPlayer(player) : undefined}
                    >
                      <Table.Td>{s.team}</Table.Td>
                      <Table.Td>{s.number}</Table.Td>
                      <Table.Td>{s.name}</Table.Td>
                      <Table.Td>{s.goals}</Table.Td>
                      <Table.Td>{s.assists}</Table.Td>
                      <Table.Td>{s.points}</Table.Td>
                      <Table.Td>{s.penalties}</Table.Td>
                      <Table.Td>{s.shootout_goals}</Table.Td>
                      <Table.Td>{s.shootout_misses}</Table.Td>
                    </Table.Tr>
                  )
                })}
              </Table.Tbody>
            </Table>
            {stats?.length === 0 && (
              <Text c="dimmed" size="sm" mt="sm">
                No stats recorded yet.
              </Text>
            )}
            {(stats?.length ?? 0) > 0 && (
              <Text c="dimmed" size="xs" mt="sm">
                Click a player linked to a profile to open it.
              </Text>
            )}
          </Tabs.Panel>
        </Tabs>
      )}

      <PlayerDetailDrawer player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
    </Stack>
  )
}
