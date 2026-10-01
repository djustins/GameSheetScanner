import { useState } from 'react'
import { Accordion, Alert, Group, Select, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listDivisionRequests } from '../api/draft'
import { listDivisions } from '../api/divisions'
import type { DivisionRequest } from '../api/types'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { divisionSeasonLabel } from '../utils/format'

const STATUSES = ['✅ Together', '✅ Apart', '❌ Split', '❌ Together', 'Not drafted'] as const

function requestType(r: DivisionRequest): string {
  return r.avoid ? 'Do not play with' : r.hard ? 'Hard' : 'Soft'
}

function requestStatus(r: DivisionRequest): string {
  if (!r.team || !r.other_team) return 'Not drafted'
  const together = r.team === r.other_team
  if (r.avoid) return together ? '❌ Together' : '✅ Apart'
  return together ? '✅ Together' : '❌ Split'
}

/** Every play-with / do-not-play-with request in the Working Division, one
 * row per pair, with whether the draft kept it. Mirrors the Streamlit app's
 * Teams → Requests tab. */
export function RequestsPage() {
  const { workingDivisionId } = useWorkingDivision()
  const [typeFilter, setTypeFilter] = useState<string | null>('All')
  const [statusFilter, setStatusFilter] = useState<string | null>('All')

  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data: requests } = useQuery({
    queryKey: ['division-requests', workingDivisionId],
    queryFn: () => listDivisionRequests(workingDivisionId!),
    enabled: workingDivisionId != null,
  })

  if (workingDivisionId == null) {
    return (
      <Stack>
        <Title order={2}>Requests</Title>
        <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
      </Stack>
    )
  }

  const division = divisions?.find((d) => d.id === workingDivisionId)
  const inDivision = (requests ?? []).filter((r) => r.other_in_division)
  const crossDivision = (requests ?? []).filter((r) => !r.other_in_division)
  const shown = inDivision.filter(
    (r) =>
      (typeFilter === 'All' || requestType(r) === typeFilter) &&
      (statusFilter === 'All' || requestStatus(r) === statusFilter)
  )
  const statuses = shown.map(requestStatus)

  return (
    <Stack>
      <Title order={2}>Requests{division ? ` — ${divisionSeasonLabel(division)}` : ''}</Title>
      <Text size="sm" c="dimmed" maw={760}>
        Play-with requests between players registered in the Working Division, one row per pair.{' '}
        <b>Soft</b>: Auto-Draft keeps them together when teams stay balanced. <b>Hard</b>: always together (siblings
        are always hard). <b>Do not play with</b>: kept on different teams. To change or remove one, open either
        player. Change the Working Division in the sidebar to see another division&apos;s.
      </Text>

      <Group>
        <Select
          label="Type"
          data={['All', 'Hard', 'Soft', 'Do not play with']}
          value={typeFilter}
          onChange={setTypeFilter}
          allowDeselect={false}
        />
        <Select
          label="Status"
          data={['All', ...STATUSES]}
          value={statusFilter}
          onChange={setStatusFilter}
          allowDeselect={false}
        />
      </Group>

      {shown.length === 0 ? (
        <Text size="sm">{inDivision.length ? 'No play-with requests match.' : 'No play-with requests yet.'}</Text>
      ) : (
        <>
          <Text size="sm" c="dimmed">
            {shown.length} pair(s) · {shown.filter((r) => r.hard).length} hard ·{' '}
            {shown.filter((r) => r.avoid).length} do not play with ·{' '}
            {STATUSES.filter((s) => statuses.includes(s))
              .map((s) => `${s}: ${statuses.filter((x) => x === s).length}`)
              .join(', ')}
          </Text>
          <Table.ScrollContainer minWidth={760}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Player</Table.Th>
                  <Table.Th>Team</Table.Th>
                  <Table.Th>Other player</Table.Th>
                  <Table.Th>Their team</Table.Th>
                  <Table.Th>Type</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Note</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {shown.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>{r.name}</Table.Td>
                    <Table.Td>{r.team ?? '—'}</Table.Td>
                    <Table.Td>{r.other_name}</Table.Td>
                    <Table.Td>{r.other_team ?? '—'}</Table.Td>
                    <Table.Td>{requestType(r)}</Table.Td>
                    <Table.Td>{requestStatus(r)}</Table.Td>
                    <Table.Td>{r.note ?? ''}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </>
      )}

      {crossDivision.length > 0 && (
        <Accordion variant="contained">
          <Accordion.Item value="cross">
            <Accordion.Control>
              Requests with a player in another division ({crossDivision.length}) — ignored by Auto-Draft
            </Accordion.Control>
            <Accordion.Panel>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Player</Table.Th>
                    <Table.Th>Other player</Table.Th>
                    <Table.Th>Type</Table.Th>
                    <Table.Th>Note</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {crossDivision.map((r) => (
                    <Table.Tr key={r.id}>
                      <Table.Td>{r.name}</Table.Td>
                      <Table.Td>{r.other_name}</Table.Td>
                      <Table.Td>{requestType(r)}</Table.Td>
                      <Table.Td>{r.note ?? ''}</Table.Td>
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
