import { useState } from 'react'
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  MultiSelect,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getEmailLog, getEmailStatus, previewRecipients, sendEmail } from '../api/email'
import type { EmailLogRow } from '../api/email'
import { listDivisionTeams } from '../api/divisions'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { divisionSeasonLabel } from '../utils/format'

const KIND_LABELS: Record<EmailLogRow['kind'], string> = {
  message: 'Message',
  test: 'Test',
  invite: 'Invitation',
  password_reset: 'Password reset',
  notice: 'Automatic notice',
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/** Admin-only: write a message to a division's parents and/or coaches, see
 * exactly who it will reach before sending, and look back at what was sent. */
export function EmailPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const { workingDivisionId, divisions } = useWorkingDivision()
  const [pickedDivision, setPickedDivision] = useState<string | null>(null)
  const [teamIds, setTeamIds] = useState<string[]>([])
  const [parents, setParents] = useState(true)
  const [coaches, setCoaches] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')

  const divisionId = pickedDivision ? Number(pickedDivision) : workingDivisionId
  const audience = {
    division_id: divisionId ?? 0,
    team_ids: teamIds.map(Number),
    parents,
    coaches,
  }

  const { data: status } = useQuery({ queryKey: ['email-status'], queryFn: getEmailStatus })
  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId!),
    enabled: divisionId != null,
  })
  const { data: preview } = useQuery({
    queryKey: ['email-recipients', audience],
    queryFn: () => previewRecipients(audience),
    enabled: divisionId != null && (parents || coaches),
  })
  const { data: log } = useQuery({ queryKey: ['email-log'], queryFn: getEmailLog })

  const recipients = parents || coaches ? (preview?.recipients ?? []) : []
  const missing = parents || coaches ? (preview?.missing ?? []) : []

  const sendMutation = useMutation({
    mutationFn: (test: boolean) => sendEmail({ ...audience, subject, body, test }),
    onSuccess: (result, test) => {
      queryClient.invalidateQueries({ queryKey: ['email-log'] })
      if (result.failed) {
        notifications.show({
          color: 'red',
          autoClose: false,
          message: `${result.failed} of ${result.sent + result.failed} emails were not sent. ${result.error ?? ''}`,
        })
        return
      }
      notifications.show({
        color: 'green',
        message: test ? `Test sent to ${user?.email}.` : `Sent to ${result.sent} ${result.sent === 1 ? 'person' : 'people'}.`,
      })
      if (!test) {
        setSubject('')
        setBody('')
      }
    },
    onError: (err) =>
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'The message was not sent.' }),
  })

  const ready = !!status?.configured && divisionId != null && subject.trim() !== '' && body.trim() !== ''

  return (
    <Stack ta="left">
      <div>
        <Title order={2} ta="center">
          Email
        </Title>
        <Text size="sm" c="dimmed" ta="center">
          Write to a division&apos;s parents and coaches. Everyone gets their own copy, so no one sees anyone else&apos;s
          address, and replies come to you.
        </Text>
      </div>

      {status && !status.configured && (
        <Alert color="yellow" title="Email isn't set up yet">
          The server has no email service configured, so nothing can be sent. See &quot;Email&quot; in the project README
          for the one-time setup; you can still write a message and check who it would reach.
        </Alert>
      )}

      <Tabs defaultValue="compose">
        <Tabs.List>
          <Tabs.Tab value="compose">Write a message</Tabs.Tab>
          <Tabs.Tab value="sent">Sent ({log?.length ?? 0})</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="compose" pt="md">
          <Stack>
            <Group align="flex-end" grow>
              <Select
                label="Division"
                data={divisions.map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))}
                value={divisionId != null ? String(divisionId) : null}
                onChange={(v) => {
                  setPickedDivision(v)
                  setTeamIds([])
                }}
                searchable
                allowDeselect={false}
              />
              <MultiSelect
                label="Teams"
                placeholder={teamIds.length ? undefined : 'Whole division'}
                description="Leave empty to include everyone, including players not on a team yet."
                data={(teams ?? []).map((t) => ({ value: String(t.id), label: t.name }))}
                value={teamIds}
                onChange={setTeamIds}
                clearable
              />
            </Group>
            <Group>
              <Text size="sm" fw={500}>
                Send to
              </Text>
              <Checkbox label="Parents" checked={parents} onChange={(e) => setParents(e.currentTarget.checked)} />
              <Checkbox label="Coaches" checked={coaches} onChange={(e) => setCoaches(e.currentTarget.checked)} />
            </Group>

            <Accordion variant="contained">
              <Accordion.Item value="recipients">
                <Accordion.Control>
                  <Group gap="sm">
                    <Text fw={600}>
                      {recipients.length} {recipients.length === 1 ? 'person' : 'people'} will get this
                    </Text>
                    {missing.length > 0 && (
                      <Badge color="orange" variant="light">
                        {missing.length} with no email on file
                      </Badge>
                    )}
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  {missing.length > 0 && (
                    <Alert color="orange" mb="sm" title="Won't be reached: no email on file">
                      {missing.join('; ')}
                    </Alert>
                  )}
                  {recipients.length === 0 ? (
                    <Text size="sm" c="dimmed">
                      Nobody yet. Pick Parents or Coaches, and check the division has contact emails.
                    </Text>
                  ) : (
                    <Table striped>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Name</Table.Th>
                          <Table.Th>Email</Table.Th>
                          <Table.Th>For</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {recipients.map((r) => (
                          <Table.Tr key={r.email}>
                            <Table.Td>{r.name}</Table.Td>
                            <Table.Td>{r.email}</Table.Td>
                            <Table.Td>{r.about}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  )}
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <TextInput label="Subject" value={subject} onChange={(e) => setSubject(e.currentTarget.value)} />
            <Textarea
              label="Message"
              description="Plain text. Leave a blank line between paragraphs; links are made clickable."
              autosize
              minRows={8}
              value={body}
              onChange={(e) => setBody(e.currentTarget.value)}
            />
            <Text size="xs" c="dimmed">
              From {status?.from ?? 'the address configured on the server'}. Replies go to {user?.email}.
            </Text>
            <Group>
              <Button
                variant="default"
                disabled={!ready}
                loading={sendMutation.isPending && sendMutation.variables === true}
                onClick={() => sendMutation.mutate(true)}
              >
                Send a test to me
              </Button>
              <Button
                disabled={!ready || recipients.length === 0}
                loading={sendMutation.isPending && sendMutation.variables === false}
                onClick={() =>
                  confirm(`Send "${subject.trim()}" to ${recipients.length} ${recipients.length === 1 ? 'person' : 'people'}?`) &&
                  sendMutation.mutate(false)
                }
              >
                Send to {recipients.length} {recipients.length === 1 ? 'person' : 'people'}
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="sent" pt="md">
          {log && log.length === 0 ? (
            <Text size="sm" c="dimmed">
              Nothing has been sent yet.
            </Text>
          ) : (
            <Accordion variant="separated">
              {(log ?? []).map((row) => (
                <Accordion.Item key={row.id} value={String(row.id)}>
                  <Accordion.Control>
                    <Group justify="space-between" wrap="nowrap">
                      <div style={{ minWidth: 0 }}>
                        <Text fw={600} truncate>
                          {row.subject}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {when(row.created_at)} · {KIND_LABELS[row.kind]}
                          {row.sent_by ? ` · ${row.sent_by}` : ''}
                          {row.division ? ` · ${row.division}` : ''}
                        </Text>
                      </div>
                      <Badge color={row.failed ? 'red' : 'green'} variant="light" style={{ flexShrink: 0 }}>
                        {row.failed ? `${row.failed} failed` : `${row.sent} sent`}
                      </Badge>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="xs">
                      {row.error && <Alert color="red">{row.error}</Alert>}
                      {row.audience && <Text size="sm">To: {row.audience}</Text>}
                      <Text size="sm" c="dimmed">
                        {row.recipients.join(', ')}
                      </Text>
                      {row.body != null && (
                        <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                          {row.body}
                        </Text>
                      )}
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              ))}
            </Accordion>
          )}
        </Tabs.Panel>
      </Tabs>
    </Stack>
  )
}
