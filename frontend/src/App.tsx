import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Center, Loader } from '@mantine/core'
import { LoginPage } from './pages/LoginPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AdminRoute } from './auth/AdminRoute'
import { PageAccess } from './auth/access'
import { AppLayout } from './layout/AppLayout'

const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const DivisionsPage = lazy(() => import('./pages/DivisionsPage').then((m) => ({ default: m.DivisionsPage })))
const PlayersPage = lazy(() => import('./pages/PlayersPage').then((m) => ({ default: m.PlayersPage })))
const StatsStandingsPage = lazy(() =>
  import('./pages/StatsStandingsPage').then((m) => ({ default: m.StatsStandingsPage }))
)
const TeamRosterPage = lazy(() => import('./pages/TeamRosterPage').then((m) => ({ default: m.TeamRosterPage })))
const TeamRostersPage = lazy(() => import('./pages/TeamRostersPage').then((m) => ({ default: m.TeamRostersPage })))
const CoachesPage = lazy(() => import('./pages/CoachesPage').then((m) => ({ default: m.CoachesPage })))
const AllParentsPage = lazy(() => import('./pages/AllParentsPage').then((m) => ({ default: m.AllParentsPage })))
const DraftPage = lazy(() => import('./pages/DraftPage').then((m) => ({ default: m.DraftPage })))
const EvalsPage = lazy(() => import('./pages/EvalsPage').then((m) => ({ default: m.EvalsPage })))
const UsagePage = lazy(() => import('./pages/UsagePage').then((m) => ({ default: m.UsagePage })))
const UserManagementPage = lazy(() =>
  import('./pages/UserManagementPage').then((m) => ({ default: m.UserManagementPage }))
)
const GamesPage = lazy(() => import('./pages/GamesPage').then((m) => ({ default: m.GamesPage })))
const ApiTokensPage = lazy(() => import('./pages/ApiTokensPage').then((m) => ({ default: m.ApiTokensPage })))

function PageFallback() {
  return (
    <Center mih="60vh">
      <Loader />
    </Center>
  )
}

function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<HomePage />} />
          <Route path="/divisions" element={<PageAccess pages={['divisions']}><DivisionsPage /></PageAccess>} />
          <Route path="/players" element={<PageAccess pages={['players']}><PlayersPage /></PageAccess>} />
          <Route path="/stats-standings" element={<PageAccess pages={['standings', 'stats']}><StatsStandingsPage /></PageAccess>} />
          <Route path="/teams/:teamId" element={<PageAccess pages={['rosters']}><TeamRosterPage /></PageAccess>} />
          <Route path="/team-rosters" element={<PageAccess pages={['rosters', 'teams']}><TeamRostersPage /></PageAccess>} />
          <Route path="/coaches" element={<PageAccess pages={['coaches']}><CoachesPage /></PageAccess>} />
          <Route path="/parents" element={<PageAccess pages={['parents']}><AllParentsPage /></PageAccess>} />
          <Route path="/draft" element={<PageAccess pages={['draft', 'teams']}><DraftPage /></PageAccess>} />
          {/* Requests moved under Draft; keep old links working. */}
          <Route path="/requests" element={<Navigate to="/draft?tab=requests" replace />} />
          <Route path="/evals" element={<PageAccess pages={['teams']}><EvalsPage /></PageAccess>} />
          <Route path="/games" element={<PageAccess pages={['schedule', 'process', 'edit']}><GamesPage /></PageAccess>} />
          <Route path="/tokens" element={<ApiTokensPage />} />
          <Route
            path="/admin/users"
            element={
              <AdminRoute>
                <UserManagementPage />
              </AdminRoute>
            }
          />
          <Route
            path="/admin/usage"
            element={
              <AdminRoute>
                <UsagePage />
              </AdminRoute>
            }
          />
        </Route>
      </Routes>
    </Suspense>
  )
}

export default App
