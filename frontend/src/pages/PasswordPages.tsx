import { useState } from 'react'
import { Alert, Anchor, Button, Image, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { Link, useSearchParams } from 'react-router-dom'
import { confirmPasswordReset, requestPasswordReset } from '../api/email'
import { ApiError } from '../api/client'

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Stack align="center" justify="center" mih="100vh">
      <Paper withBorder shadow="sm" p="xl" w={380}>
        <Stack>
          <Image src="/logo.png" alt="Team Pittsburgh Ball Hockey" bg="#000000" p={10} radius={16} />
          <Title order={3}>{title}</Title>
          {children}
          <Anchor component={Link} to="/login" size="sm">
            Back to sign in
          </Anchor>
        </Stack>
      </Paper>
    </Stack>
  )
}

/** "Forgot password?": ask for a reset link by email. */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await requestPasswordReset(email)
      setDone(true)
    } catch {
      setError("Couldn't reach the server. Try again in a minute.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card title="Reset your password">
      {done ? (
        // The same message whether or not the address has an account.
        <Alert color="green">
          If {email} has an account, a reset link is on its way. It works once and expires in two hours. Check your spam
          folder if it doesn&apos;t arrive.
        </Alert>
      ) : (
        <form onSubmit={handleSubmit}>
          <Stack>
            <Text size="sm" c="dimmed">
              Enter the email you sign in with and we&apos;ll send you a link to choose a new password.
            </Text>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              autoFocus
              required
            />
            <Button type="submit" loading={submitting} fullWidth>
              Email me a reset link
            </Button>
          </Stack>
        </form>
      )}
    </Card>
  )
}

/** Where an emailed link lands (a reset or an invitation): choose a password. */
export function ResetPasswordPage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (password !== again) {
      setError("The two passwords don't match.")
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await confirmPasswordReset(token, password)
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server. Try again in a minute.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card title="Choose your password">
      {!token ? (
        <Alert color="red">This link is incomplete. Open it again from the email, or ask for a new one.</Alert>
      ) : done ? (
        <Alert color="green">Your password is set. Go back to sign in and use it.</Alert>
      ) : (
        <form onSubmit={handleSubmit}>
          <Stack>
            {error && <Alert color="red">{error}</Alert>}
            <PasswordInput
              label="New password"
              description="At least 8 characters."
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              autoFocus
              required
            />
            <PasswordInput label="Type it again" value={again} onChange={(e) => setAgain(e.currentTarget.value)} required />
            <Button type="submit" loading={submitting} fullWidth>
              Set password
            </Button>
          </Stack>
        </form>
      )}
    </Card>
  )
}
