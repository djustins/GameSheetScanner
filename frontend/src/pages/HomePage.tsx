import { Alert, Anchor, Card, Stack, Text, Title } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import { useAuth } from '../auth/AuthContext'
import { useAccess } from '../auth/access'

// A video walk-through of what's new in the app.
const LATEST_UPDATES_URL = 'https://youtu.be/5NCeMgRYGO8'

interface PageGuide {
  to: string
  title: string
  description: string
  // Page keys (game_sheet_core.PAGES) granting access; omitted = always open.
  pages?: string[]
}

const PAGE_GUIDES: PageGuide[] = [
  {
    to: '/games',
    title: 'Games',
    description:
      "Schedule & Results, Import Scoresheets (upload and extract with Claude), and Manage Games — everything about the Working Division's games.",
    pages: ['schedule', 'process', 'edit'],
  },
  {
    to: '/stats-standings',
    title: 'Stats & Standings',
    description: "Live standings and every player's stats for the Working Division.",
    pages: ['standings', 'stats'],
  },
  {
    to: '/team-rosters',
    title: 'Team Rosters',
    description:
      "Each team's players and their stats in the Working Division, plus everyone registered in it.",
    pages: ['rosters', 'teams'],
  },
  {
    to: '/roster-management',
    title: 'Roster Management',
    description:
      'Draft the division (live or Auto-Draft), review play-with requests, trade players between teams, and place anyone not on a team yet.',
    pages: ['draft', 'teams', 'rosters'],
  },
  {
    to: '/divisions',
    title: 'Divisions',
    description: 'Create/delete divisions, manage teams and players within one, coach carryover, schedule, and export.',
    pages: ['divisions'],
  },
  {
    to: '/players',
    title: 'All Players',
    description: 'Every player registered in the system, with season history, evaluations, and parent/sibling links.',
    pages: ['players'],
  },
  {
    to: '/coaches',
    title: 'All Coaches',
    description: 'Coach profiles, their registered children, and teams coached — global across every division.',
    pages: ['coaches'],
  },
  {
    to: '/parents',
    title: 'All Parents',
    description: 'Every parent/guardian on file and their children, matched automatically from contact info.',
    pages: ['parents'],
  },
]

export function HomePage() {
  const { user } = useAuth()
  const { canViewAny } = useAccess()
  const navigate = useNavigate()
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })

  const guides: PageGuide[] = user?.is_admin
    ? [...PAGE_GUIDES, { to: '/admin/users', title: 'User Management (admins only)', description: 'Create accounts and control who can see which pages, using named roles instead of picking pages one by one.' }]
    : PAGE_GUIDES

  return (
    <Stack maw={800}>
      <Title order={2}>Welcome to Team Pittsburgh Ball Hockey</Title>
      <Text size="sm" c="dimmed">
        This app turns handwritten game sheets into stored stats, standings, and rosters — here&apos;s what each
        page does.
      </Text>
      <Text size="sm">
        <Anchor href={LATEST_UPDATES_URL} target="_blank" rel="noopener noreferrer" fw={600}>
          ▶ Latest updates
        </Anchor>{' '}
        <Text span c="dimmed">
          (video, opens in a new tab)
        </Text>
      </Text>

      {divisions && divisions.length === 0 ? (
        <Alert color="yellow">
          👉 <strong>Start here:</strong> no divisions exist yet. Open <strong>Divisions</strong> below to create
          one before doing anything else — everything else in the app (games, standings, stats, rosters) is
          scoped to a division.
        </Alert>
      ) : (
        <Alert color="blue">
          👉 <strong>Start here:</strong> choose your <strong>Working Division</strong> in the sidebar — Team
          Rosters, Games, Stats & Standings and Roster Management all show that division. It&apos;s shared with the Streamlit
          app, which picks it up the next time it&apos;s opened.
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
        {guides.map((g) => {
          const locked = g.pages != null && !canViewAny(g.pages)
          return (
            <Card
              key={g.to}
              withBorder
              shadow="none"
              style={{ cursor: locked ? 'default' : 'pointer', opacity: locked ? 0.55 : 1 }}
              onClick={locked ? undefined : () => navigate(g.to)}
            >
              <Text fw={700}>
                {locked ? '🔒 ' : ''}
                {g.title}
              </Text>
              <Text size="sm" c="dimmed">
                {g.description}
                {locked ? " — your role doesn't include this page; ask an admin if you need it." : ''}
              </Text>
            </Card>
          )
        })}
      </Stack>
    </Stack>
  )
}
