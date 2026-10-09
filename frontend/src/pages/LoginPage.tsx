import { useState } from 'react'
import { Alert, Anchor, Button, Image, Paper, PasswordInput, Stack, TextInput, Title } from '@mantine/core'
import { Link, useNavigate } from 'react-router-dom'
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
            <Image src="/logo.png" alt="Team Pittsburgh Ball Hockey" bg="#000000" p={10} radius={16} />
            <Title order={3}>Team Manager</Title>
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
            <Anchor component={Link} to="/forgot-password" size="sm">
              Forgot your password?
            </Anchor>
          </Stack>
        </form>
      </Paper>
    </Stack>
  )
}
