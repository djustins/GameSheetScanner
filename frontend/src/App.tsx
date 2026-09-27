import { Navigate, Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { DivisionsPage } from './pages/DivisionsPage'
import { PlayersPage } from './pages/PlayersPage'
import { StatsStandingsPage } from './pages/StatsStandingsPage'
import { TeamRosterPage } from './pages/TeamRosterPage'
import { CoachesPage } from './pages/CoachesPage'
import { DraftPage } from './pages/DraftPage'
import { UserManagementPage } from './pages/UserManagementPage'
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
        <Route path="/" element={<Navigate to="/divisions" replace />} />
        <Route path="/divisions" element={<DivisionsPage />} />
        <Route path="/players" element={<PlayersPage />} />
        <Route path="/stats-standings" element={<StatsStandingsPage />} />
        <Route path="/teams/:teamId" element={<TeamRosterPage />} />
        <Route path="/coaches" element={<CoachesPage />} />
        <Route path="/draft" element={<DraftPage />} />
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
