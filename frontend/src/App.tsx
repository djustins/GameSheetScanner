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
const RosterManagementPage = lazy(() =>
  import('./pages/RosterManagementPage').then((m) => ({ default: m.RosterManagementPage }))
)
const EvalsPage = lazy(() => import('./pages/EvalsPage').then((m) => ({ default: m.EvalsPage })))
const EmailPage = lazy(() => import('./pages/EmailPage').then((m) => ({ default: m.EmailPage })))
const ForgotPasswordPage = lazy(() =>
  import('./pages/PasswordPages').then((m) => ({ default: m.ForgotPasswordPage }))
)
const ResetPasswordPage = lazy(() => import('./pages/PasswordPages').then((m) => ({ default: m.ResetPasswordPage })))
const AccountPage = lazy(() => import('./pages/AccountPage').then((m) => ({ default: m.AccountPage })))
const UsagePage = lazy(() => import('./pages/UsagePage').then((m) => ({ default: m.UsagePage })))
const UserManagementPage = lazy(() =>
  import('./pages/UserManagementPage').then((m) => ({ default: m.UserManagementPage }))
)
const GamesPage = lazy(() => import('./pages/GamesPage').then((m) => ({ default: m.GamesPage })))

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
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
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
          <Route
            path="/roster-management"
            element={<PageAccess pages={['draft', 'teams', 'rosters']}><RosterManagementPage /></PageAccess>}
          />
          {/* Draft and Requests are sections of Roster Management now; keep old links working. */}
          <Route path="/draft" element={<Navigate to="/roster-management" replace />} />
          <Route path="/requests" element={<Navigate to="/roster-management?tab=requests" replace />} />
          <Route path="/evals" element={<PageAccess pages={['teams']}><EvalsPage /></PageAccess>} />
          <Route path="/games" element={<PageAccess pages={['schedule', 'process', 'edit']}><GamesPage /></PageAccess>} />
          <Route path="/account" element={<AccountPage />} />
          {/* API tokens are a section of My Account now. */}
          <Route path="/tokens" element={<Navigate to="/account" replace />} />
          <Route
            path="/admin/users"
            element={
              <AdminRoute>
                <UserManagementPage />
              </AdminRoute>
            }
          />
          <Route
            path="/admin/email"
            element={
              <AdminRoute>
                <EmailPage />
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
