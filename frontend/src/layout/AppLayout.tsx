import { useEffect, useRef } from 'react'
import {
  AppShell,
  Badge,
  Box,
  Burger,
  Group,
  Image,
  NavLink,
  SegmentedControl,
  Select,
  Tabs,
  Text,
  Title,
  useMantineColorScheme,
} from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { recordPageView } from '../api/users'
import { TermsGate } from '../components/TermsGate'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useAccess } from '../auth/access'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { divisionSeasonLabel } from '../utils/format'

interface NavItem {
  to: string
  label: string
  // Page keys (game_sheet_core.PAGES) granting access; omitted = always shown.
  pages?: string[]
}

// The app works in two modes, picked in the left panel: one division's season
// (the day-to-day work) or global (setup and reference across all divisions).
// Each mode's pages are the tabs along the top.
type Mode = 'division' | 'global'

const GLOBAL_NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Home' },
  { to: '/divisions', label: 'Divisions', pages: ['divisions'] },
  { to: '/players', label: 'All Players', pages: ['players'] },
  { to: '/coaches', label: 'All Coaches', pages: ['coaches'] },
  { to: '/parents', label: 'All Parents', pages: ['parents'] },
]

const SCOPED_NAV_ITEMS: NavItem[] = [
  { to: '/team-rosters', label: 'Team Rosters', pages: ['rosters', 'teams'] },
  { to: '/evals', label: 'Evals', pages: ['teams'] },
  { to: '/games', label: 'Games', pages: ['schedule', 'process', 'edit'] },
  { to: '/stats-standings', label: 'Stats & Standings', pages: ['standings', 'stats'] },
  { to: '/roster-management', label: 'Roster Management', pages: ['draft', 'teams', 'rosters'] },
]

export function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure()
  const { colorScheme, setColorScheme } = useMantineColorScheme()
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const { workingDivisionId, setWorkingDivisionId, divisions, loading } = useWorkingDivision()

  const { canViewAny } = useAccess()
  const allowed = (item: NavItem) => !item.pages || canViewAny(item.pages)

  const globalItems = (
    user?.is_admin
      ? [...GLOBAL_NAV_ITEMS, { to: '/admin/users', label: 'User Management' }, { to: '/admin/email', label: 'Email' }, { to: '/admin/usage', label: 'Usage' }]
      : GLOBAL_NAV_ITEMS
  ).filter(allowed)
  const scopedItems = SCOPED_NAV_ITEMS.filter(allowed)

  // Log each page the signed-in user opens (see GET /usage). Never worth an
  // error message if it fails.
  useEffect(() => {
    recordPageView(location.pathname).catch(() => {})
  }, [location.pathname])

  function isActive(to: string) {
    if (to === '/') return location.pathname === '/'
    // A single team's roster page (/teams/:teamId) belongs to the Team Rosters tab.
    if (to === '/team-rosters' && location.pathname.startsWith('/teams/')) return true
    return location.pathname.startsWith(to)
  }

  const mode: Mode = scopedItems.some((item) => isActive(item.to)) ? 'division' : 'global'
  const tabItems = mode === 'division' ? scopedItems : globalItems
  const activeTab = tabItems.find((item) => isActive(item.to))?.to ?? null

  // Switching modes returns to the page last open in that mode.
  const lastPath = useRef<Record<Mode, string | null>>({ division: null, global: null })
  useEffect(() => {
    lastPath.current[mode] = location.pathname
  }, [mode, location.pathname])

  function switchMode(next: Mode) {
    close()
    if (next === mode) return
    const items = next === 'division' ? scopedItems : globalItems
    navigate(lastPath.current[next] ?? items[0]?.to ?? '/')
  }

  const workingDivision = divisions.find((d) => d.id === workingDivisionId)

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header bg="var(--app-secondary-bg)">
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={3} visibleFrom="sm">
              Team Pittsburgh Team Manager
            </Title>
            {/* Names the current scope, since the left panel is collapsed on a phone. */}
            <Badge variant="light">
              {mode === 'global' ? 'Global' : workingDivision ? divisionSeasonLabel(workingDivision) : 'No division'}
            </Badge>
          </Group>
          <Group wrap="nowrap">
            {user?.is_admin && (
              <Text
                size="sm"
                c="var(--app-heading)"
                style={{ cursor: 'pointer' }}
                visibleFrom="sm"
                onClick={() => navigate('/admin/email')}
              >
                Email
              </Text>
            )}
            <Text size="sm" c="var(--app-heading)" style={{ cursor: 'pointer' }} onClick={() => navigate('/account')}>
              {user?.display_name || 'My Account'}
            </Text>
            <Text size="sm" c="var(--app-heading)" style={{ cursor: 'pointer' }} onClick={logout}>
              Log out
            </Text>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar bg="var(--app-secondary-bg)">
        {/* Scrolls as a block: the navbar itself is a flex column that would squash its children. */}
        <Box p="md" style={{ overflowY: 'auto' }}>
          {/* The logo sits on black in both themes, as in the Streamlit sidebar. */}
          <Image src="/logo.png" alt="Team Pittsburgh Ball Hockey" bg="#000000" p={10} radius={16} maw={200} mx="auto" mb="md" />
          {scopedItems.length > 0 && (
            <>
              <NavLink
                label="Division"
                description="One season's rosters, games and stats"
                active={mode === 'division'}
                onClick={() => switchMode('division')}
              />
              {/* Only the division's own pages obey this, so it is hidden in Global mode. */}
              {mode === 'division' && (
                <Select
                  aria-label="Working Division"
                  placeholder={divisions.length === 0 ? 'No divisions yet' : 'Choose a division'}
                  data={divisions.map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))}
                  value={workingDivisionId != null ? String(workingDivisionId) : null}
                  onChange={(v) => setWorkingDivisionId(v ? Number(v) : null)}
                  disabled={loading || divisions.length === 0}
                  searchable
                  mt={4}
                  mb="md"
                  size="sm"
                />
              )}
            </>
          )}
          <NavLink
            label="Global"
            description="Setup and reference across all divisions"
            active={mode === 'global'}
            onClick={() => switchMode('global')}
          />
          <SegmentedControl
            aria-label="Theme"
            data={[
              { value: 'dark', label: 'Dark' },
              { value: 'light', label: 'Light' },
            ]}
            value={colorScheme === 'light' ? 'light' : 'dark'}
            onChange={(v) => setColorScheme(v as 'dark' | 'light')}
            fullWidth
            size="xs"
            mt="xl"
          />
        </Box>
      </AppShell.Navbar>
      <AppShell.Main>
        <TermsGate />
        <Tabs value={activeTab} onChange={(to) => to && navigate(to)} mb="md">
          {/* One row that scrolls sideways on a phone instead of wrapping. */}
          <Tabs.List style={{ flexWrap: 'nowrap', overflowX: 'auto' }}>
            {tabItems.map((item) => (
              <Tabs.Tab key={item.to} value={item.to} style={{ flexShrink: 0 }}>
                {item.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}
