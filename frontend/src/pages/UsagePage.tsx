import { useState } from 'react'
import { Alert, Group, SegmentedControl, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getUsage } from '../api/users'

// Route -> the name its page goes by in the sidebar.
const PAGE_NAMES: Record<string, string> = {
  '/': 'Home',
  '/divisions': 'Divisions',
  '/players': 'All Players',
  '/coaches': 'All Coaches',
  '/parents': 'All Parents',
  '/team-rosters': 'Team Rosters (Stats)',
  '/teams/:id': 'Team roster (one team)',
  '/evals': 'Evals',
  '/games': 'Games',
  '/stats-standings': 'Stats & Standings',
  '/roster-management': 'Roster Management',
  '/draft': 'Draft (old link)',
  '/account': 'My Account',
  '/tokens': 'API Tokens (old link)',
  '/admin/users': 'User Management',
  '/admin/usage': 'Usage',
  '/admin/email': 'Email',
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
// A plain date ("2026-10-06") must not be shifted by the viewer's time zone.
const day = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })

/** Admin-only: who has been using this app, counted by the API rather than in the browser. */
export function UsagePage() {
  const [days, setDays] = useState('30')
  const { data, isLoading, error } = useQuery({ queryKey: ['usage', days], queryFn: () => getUsage(Number(days)) })

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Usage</Title>
          <Text size="sm" c="dimmed">
            Sign-ins and pages opened in this app, counted on the server, so ad blockers don&apos;t affect it.
          </Text>
        </div>
        <SegmentedControl
          value={days}
          onChange={setDays}
          data={[
            { value: '7', label: '7 days' },
            { value: '30', label: '30 days' },
            { value: '90', label: '90 days' },
          ]}
        />
      </Group>

      {error && <Alert color="red">Couldn&apos;t load usage.</Alert>}
      {!isLoading && data && data.users.length === 0 && (
        <Alert color="blue">Nothing recorded in this period yet.</Alert>
      )}

      {data && data.users.length > 0 && (
        <>
          <Title order={4}>People ({data.users.length})</Title>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Person</Table.Th>
                <Table.Th>Last seen</Table.Th>
                <Table.Th ta="right">Days active</Table.Th>
                <Table.Th ta="right">Sign-ins</Table.Th>
                <Table.Th ta="right">Pages opened</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.users.map((u) => (
                <Table.Tr key={u.user_id}>
                  <Table.Td>
                    {u.display_name || u.email}
                    {u.display_name && (
                      <Text span size="xs" c="dimmed" ml={6}>
                        {u.email}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{when(u.last_seen)}</Table.Td>
                  <Table.Td ta="right">{u.days_active}</Table.Td>
                  <Table.Td ta="right">{u.logins}</Table.Td>
                  <Table.Td ta="right">{u.page_views}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="xl">
            <div>
              <Title order={4} mb="xs">
                Pages
              </Title>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Page</Table.Th>
                    <Table.Th ta="right">Opened</Table.Th>
                    <Table.Th ta="right">People</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.pages.map((p) => (
                    <Table.Tr key={p.path}>
                      <Table.Td>{PAGE_NAMES[p.path] ?? p.path}</Table.Td>
                      <Table.Td ta="right">{p.views}</Table.Td>
                      <Table.Td ta="right">{p.users}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
            <div>
              <Title order={4} mb="xs">
                By day
              </Title>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Day</Table.Th>
                    <Table.Th ta="right">People</Table.Th>
                    <Table.Th ta="right">Sign-ins</Table.Th>
                    <Table.Th ta="right">Pages opened</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.by_day.map((d) => (
                    <Table.Tr key={d.date}>
                      <Table.Td>{day(d.date)}</Table.Td>
                      <Table.Td ta="right">{d.users}</Table.Td>
                      <Table.Td ta="right">{d.logins}</Table.Td>
                      <Table.Td ta="right">{d.page_views}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          </SimpleGrid>
        </>
      )}
    </Stack>
  )
}
