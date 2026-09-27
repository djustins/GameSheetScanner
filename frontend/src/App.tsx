import { Navigate, Route, Routes } from 'react-router-dom'
import { LoginPage } from './pages/LoginPage'
import { DivisionsPage } from './pages/DivisionsPage'
import { PlayersPage } from './pages/PlayersPage'
import { StatsStandingsPage } from './pages/StatsStandingsPage'
import { ProtectedRoute } from './auth/ProtectedRoute'
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
      </Route>
    </Routes>
  )
}

export default App
