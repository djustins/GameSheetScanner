import { useState } from 'react'
import { Alert, Button, Paper, PasswordInput, Stack, TextInput, Title } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../api/client'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate('/')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Login failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Stack align="center" justify="center" mih="100vh">
      <Paper withBorder shadow="sm" p="xl" w={360}>
        <form onSubmit={handleSubmit}>
          <Stack>
            <Title order={2}>GameSheetScanner</Title>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput
              label="Email"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              autoFocus
              required
            />
            <PasswordInput
              label="Password"
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
            />
            <Button type="submit" loading={submitting} fullWidth>
              Log in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Stack>
  )
}
