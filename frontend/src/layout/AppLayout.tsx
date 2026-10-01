import { AppShell, Burger, Group, NavLink, Select, Text, Title } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
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

const GLOBAL_NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Home' },
  { to: '/divisions', label: 'Divisions', pages: ['divisions'] },
  { to: '/players', label: 'All Players', pages: ['players'] },
  { to: '/coaches', label: 'All Coaches', pages: ['coaches'] },
  { to: '/parents', label: 'All Parents', pages: ['parents'] },
]

const SCOPED_NAV_ITEMS: NavItem[] = [
  { to: '/team-rosters', label: 'Team Rosters', pages: ['rosters', 'teams'] },
  { to: '/requests', label: 'Requests', pages: ['teams'] },
  { to: '/games', label: 'Games', pages: ['schedule', 'process', 'edit'] },
  { to: '/stats-standings', label: 'Stats & Standings', pages: ['standings', 'stats'] },
  { to: '/draft', label: 'Draft', pages: ['draft'] },
]

export function AppLayout() {
  const [opened, { toggle }] = useDisclosure()
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const { workingDivisionId, setWorkingDivisionId, divisions, loading } = useWorkingDivision()

  const { canViewAny } = useAccess()
  const allowed = (item: NavItem) => !item.pages || canViewAny(item.pages)

  const globalItems = (
    user?.is_admin ? [...GLOBAL_NAV_ITEMS, { to: '/admin/users', label: 'User Management' }] : GLOBAL_NAV_ITEMS
  ).filter(allowed)
  const scopedItems = SCOPED_NAV_ITEMS.filter(allowed)

  function isActive(to: string) {
    return to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
  }

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={3}>GameSheetScanner</Title>
          </Group>
          <Group>
            <Text size="sm" c="dimmed">
              {user?.display_name}
            </Text>
            <Text size="sm" c="blue" style={{ cursor: 'pointer' }} onClick={() => navigate('/tokens')}>
              API Tokens
            </Text>
            <Text size="sm" c="blue" style={{ cursor: 'pointer' }} onClick={logout}>
              Log out
            </Text>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="md">
        <Select
          label="Working Division"
          placeholder={divisions.length === 0 ? 'No divisions yet' : 'Choose a division'}
          data={divisions.map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))}
          value={workingDivisionId != null ? String(workingDivisionId) : null}
          onChange={(v) => setWorkingDivisionId(v ? Number(v) : null)}
          disabled={loading || divisions.length === 0}
          searchable
          mb="md"
          size="sm"
        />
        {globalItems.map((item) => (
          <NavLink key={item.to} label={item.label} active={isActive(item.to)} onClick={() => navigate(item.to)} />
        ))}
        {scopedItems.length > 0 && (
          <Text size="xs" c="dimmed" mt="md" mb={4} tt="uppercase" fw={700}>
            Working Division
          </Text>
        )}
        {scopedItems.map((item) => (
          <NavLink key={item.to} label={item.label} active={isActive(item.to)} onClick={() => navigate(item.to)} />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}
