import { Alert, Card, Stack, Text, Title } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import { useAuth } from '../auth/AuthContext'

const PAGE_GUIDES = [
  {
    to: '/games',
    title: 'Games',
    description:
      'Import Scoresheets (upload and extract with Claude) and Manage Games — everything about the working division\'s games.',
  },
  {
    to: '/stats-standings',
    title: 'Stats & Standings',
    description: 'Live standings and every player\'s stats, both scoped to a division you pick.',
  },
  {
    to: '/divisions',
    title: 'Divisions',
    description: 'Create/delete divisions, manage teams and players within one, coach carryover, schedule, and export.',
  },
  {
    to: '/coaches',
    title: 'Coaches',
    description: 'Coach profiles, their registered children, and teams coached — global across every division.',
  },
  {
    to: '/draft',
    title: 'Draft',
    description: 'A live snake-order draft, or auto-draft the whole pool at once.',
  },
  {
    to: '/players',
    title: 'All Players',
    description: 'Every player registered in the system, with season history, evaluations, and parent/sibling links.',
  },
]

export function HomePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })

  const guides = user?.is_admin
    ? [...PAGE_GUIDES, { to: '/admin/users', title: 'User Management (admins only)', description: 'Create accounts and control who can see which pages, using named roles instead of picking pages one by one.' }]
    : PAGE_GUIDES

  return (
    <Stack maw={800}>
      <Title order={2}>Welcome to Team Pittsburgh Ball Hockey</Title>
      <Text size="sm" c="dimmed">
        This app turns handwritten game sheets into stored stats, standings, and rosters — here&apos;s what each
        page does.
      </Text>

      {divisions && divisions.length === 0 ? (
        <Alert color="yellow">
          👉 <strong>Start here:</strong> no divisions exist yet. Open <strong>Divisions</strong> below to create
          one before doing anything else — everything else in the app (games, standings, stats, rosters) is
          scoped to a division.
        </Alert>
      ) : (
        <Alert color="blue">
          👉 <strong>Start here:</strong> pick a division on the <strong>Divisions</strong>, <strong>Games</strong>,
          or <strong>Stats & Standings</strong> page — each has its own division picker at the top.
        </Alert>
      )}

      <Title order={4} mt="md">
        What this app does, in short
      </Title>
      <Text size="sm">
        1. You upload a scanned or photographed game sheet.
        <br />
        2. Claude (Anthropic&apos;s AI) reads the handwriting and extracts the teams, score, goals, assists,
        penalties, and shootout rounds.
        <br />
        3. You review and correct that extraction before anything is saved — nothing is written to the database
        without your confirmation.
        <br />
        4. From there, standings, player stats, and rosters are all computed automatically from the games
        you&apos;ve entered — no separate data entry.
      </Text>

      <Title order={4} mt="md">
        Page guide
      </Title>
      <Stack gap="xs">
        {guides.map((g) => (
          <Card key={g.to} withBorder shadow="none" style={{ cursor: 'pointer' }} onClick={() => navigate(g.to)}>
            <Text fw={700}>{g.title}</Text>
            <Text size="sm" c="dimmed">
              {g.description}
            </Text>
          </Card>
        ))}
      </Stack>
    </Stack>
  )
}
