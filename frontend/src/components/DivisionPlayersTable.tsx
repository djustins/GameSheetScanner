import { useState } from 'react'
import { Table, Text, Tooltip } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listDivisionPlayers } from '../api/divisions'
import type { Player } from '../api/types'
import { ExperienceBadge } from './ExperienceBadge'
import { PlayerDetailDrawer } from './PlayerDetailDrawer'
import { gradeBreakdown } from '../utils/grades'

export function DivisionPlayersTable({ divisionId }: { divisionId: number }) {
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)

  const { data: players } = useQuery({
    queryKey: ['division-players', String(divisionId)],
    queryFn: () => listDivisionPlayers(divisionId),
  })

  return (
    <>
      <Text size="sm" c="dimmed" mb="xs">
        {players?.length ?? 0} player(s)
        {players && players.length > 0 && (() => {
          const { gradedCount, breakdown } = gradeBreakdown(players.map((p) => p.grade))
          return ` · ${gradedCount} graded${gradedCount > 0 ? ` · ${breakdown}` : ''}`
        })()}
        {players?.some((p) => p.grade_is_carryover) && ' · * carried over from a different division'}
      </Text>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Teams</Table.Th>
            <Table.Th>Grade</Table.Th>
            <Table.Th>Note</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(players ?? []).map((p) => (
            <Table.Tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedPlayer(p)}>
              <Table.Td>{p.name}</Table.Td>
              <Table.Td>{p.teams.join(', ') || '—'}</Table.Td>
              <Table.Td>
                {p.grade && p.grade_is_carryover ? (
                  <Tooltip label="Carried over from a different division — not evaluated in this one yet">
                    <Text span c="orange" fs="italic" fw={600}>
                      {p.grade} *
                    </Text>
                  </Tooltip>
                ) : (
                  p.grade ?? '—'
                )}
              </Table.Td>
              <Table.Td>
                <ExperienceBadge note={p.note} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {players?.length === 0 && (
        <Text c="dimmed" size="sm" mt="sm">
          No players in this division yet.
        </Text>
      )}

      <PlayerDetailDrawer player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
    </>
  )
}
