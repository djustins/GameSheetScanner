import { AppShell, Burger, Group, NavLink, Text, Title } from '@mantine/core'
import { useDisclosure } from '@mantine/hooks'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const NAV_ITEMS = [
  { to: '/', label: 'Home' },
  { to: '/divisions', label: 'Divisions' },
  { to: '/players', label: 'All Players' },
  { to: '/games', label: 'Games' },
  { to: '/stats-standings', label: 'Stats & Standings' },
  { to: '/coaches', label: 'Coaches' },
  { to: '/draft', label: 'Draft' },
]

export function AppLayout() {
  const [opened, { toggle }] = useDisclosure()
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const navItems = user?.is_admin ? [...NAV_ITEMS, { to: '/admin/users', label: 'User Management' }] : NAV_ITEMS

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }}
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
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            label={item.label}
            active={item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)}
            onClick={() => navigate(item.to)}
          />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}
