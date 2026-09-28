import { Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { HomePage } from './pages/HomePage'
import { DivisionsPage } from './pages/DivisionsPage'
import { PlayersPage } from './pages/PlayersPage'
import { StatsStandingsPage } from './pages/StatsStandingsPage'
import { TeamRosterPage } from './pages/TeamRosterPage'
import { CoachesPage } from './pages/CoachesPage'
import { DraftPage } from './pages/DraftPage'
import { UserManagementPage } from './pages/UserManagementPage'
import { GamesPage } from './pages/GamesPage'
import { ApiTokensPage } from './pages/ApiTokensPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { AdminRoute } from './auth/AdminRoute'
import { AppLayout } from './layout/AppLayout'

function App() {
  return (
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
        <Route path="/divisions" element={<DivisionsPage />} />
        <Route path="/players" element={<PlayersPage />} />
        <Route path="/stats-standings" element={<StatsStandingsPage />} />
        <Route path="/teams/:teamId" element={<TeamRosterPage />} />
        <Route path="/coaches" element={<CoachesPage />} />
        <Route path="/draft" element={<DraftPage />} />
        <Route path="/games" element={<GamesPage />} />
        <Route path="/tokens" element={<ApiTokensPage />} />
        <Route
          path="/admin/users"
          element={
            <AdminRoute>
              <UserManagementPage />
            </AdminRoute>
          }
        />
      </Route>
    </Routes>
  )
}

export default App
