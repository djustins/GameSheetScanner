import { Alert, Anchor, Badge, Button, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getServices } from '../api/services'
import type { ServiceCheck, ServiceNotice, ServiceProvider } from '../api/services'

const INDICATORS: Record<string, { label: string; color: string }> = {
  none: { label: 'Operational', color: 'green' },
  minor: { label: 'Minor problem', color: 'yellow' },
  major: { label: 'Major problem', color: 'orange' },
  critical: { label: 'Outage', color: 'red' },
  maintenance: { label: 'Under maintenance', color: 'blue' },
  unknown: { label: 'Unknown', color: 'gray' },
}

const CHECK_STATES: Record<ServiceCheck['state'], { label: string; color: string }> = {
  ok: { label: 'OK', color: 'green' },
  warning: { label: 'Attention', color: 'yellow' },
  problem: { label: 'Problem', color: 'red' },
  unknown: { label: 'Unknown', color: 'gray' },
}

const when = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    : ''

function Notice({ notice, maintenance }: { notice: ServiceNotice; maintenance: boolean }) {
  return (
    <Alert color={maintenance ? 'blue' : 'orange'} p="sm" title={notice.name}>
      <Stack gap={4}>
        {maintenance && notice.scheduled_for && (
          <Text size="sm" fw={600}>
            {when(notice.scheduled_for)}
            {notice.scheduled_until ? ` to ${when(notice.scheduled_until)}` : ''} (your time)
          </Text>
        )}
        {!maintenance && (
          <Text size="xs" c="dimmed" tt="capitalize">
            {(notice.status ?? '').replace(/_/g, ' ')}
            {notice.updated_at ? ` · updated ${when(notice.updated_at)}` : ''}
          </Text>
        )}
        {notice.body && (
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} lineClamp={6}>
            {notice.body}
          </Text>
        )}
        {notice.url && (
          <Anchor href={notice.url} target="_blank" rel="noopener noreferrer" size="sm">
            Details
          </Anchor>
        )}
      </Stack>
    </Alert>
  )
}

function ProviderCard({ provider }: { provider: ServiceProvider }) {
  const indicator = INDICATORS[provider.indicator] ?? INDICATORS.unknown
  return (
    <Card withBorder radius="md" padding="md" ta="left" bg="var(--app-panel-bg)">
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <div>
            <Title order={4}>{provider.name}</Title>
            <Text size="sm" c="dimmed">
              {provider.role}
            </Text>
          </div>
          <Badge color={indicator.color} variant="filled" style={{ flexShrink: 0 }}>
            {indicator.label}
          </Badge>
        </Group>
        <Text size="sm">{provider.description}</Text>
        {provider.degraded.length > 0 && (
          <Text size="sm" c="dimmed">
            Affected: {provider.degraded.join('; ')}
          </Text>
        )}
        {provider.incidents.map((n, i) => (
          <Notice key={`i${i}`} notice={n} maintenance={false} />
        ))}
        {provider.maintenances.map((n, i) => (
          <Notice key={`m${i}`} notice={n} maintenance />
        ))}
        <Group gap="md">
          <Anchor href={provider.status_url} target="_blank" rel="noopener noreferrer" size="sm">
            Status page
          </Anchor>
          <Anchor href={provider.dashboard_url} target="_blank" rel="noopener noreferrer" size="sm">
            Dashboard
          </Anchor>
        </Group>
      </Stack>
    </Card>
  )
}

/** Admin-only: the state of every service the app depends on, with each one's
 * own incident and maintenance notices, plus the app's own health checks. */
export function ServicesPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['services'],
    queryFn: () => getServices(false),
    refetchInterval: 120_000,
  })
  const refresh = async () => queryClient.setQueryData(['services'], await getServices(true))

  const providers = data?.providers ?? []
  const checks = data?.checks ?? []
  const incidents = providers.reduce((n, p) => n + p.incidents.length, 0)
  const maintenances = providers.reduce((n, p) => n + p.maintenances.length, 0)
  const troubled = providers.filter((p) => !['none', 'unknown'].includes(p.indicator)).length
  const problems = checks.filter((c) => c.state === 'problem').length
  const allClear = incidents === 0 && troubled === 0 && problems === 0

  return (
    <Stack ta="left">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Services</Title>
          <Text size="sm" c="dimmed">
            Everything the app runs on, and what each service is reporting about itself.
            {data ? ` Checked ${when(data.checked_at)}.` : ''}
          </Text>
        </div>
        <Button variant="default" loading={isFetching} onClick={refresh}>
          Check again
        </Button>
      </Group>

      {error && <Alert color="red">Couldn&apos;t load the service checks.</Alert>}
      {isLoading && <Text c="dimmed">Checking each service…</Text>}

      {data && (
        <Alert color={allClear ? (maintenances ? 'blue' : 'green') : 'orange'} title={allClear ? 'Everything is working' : 'Something needs attention'}>
          {[
            problems ? `${problems} of this app's own checks ${problems === 1 ? 'is' : 'are'} failing` : null,
            incidents ? `${incidents} open incident${incidents === 1 ? '' : 's'}` : null,
            troubled && !incidents ? `${troubled} service${troubled === 1 ? '' : 's'} reporting a problem` : null,
            maintenances ? `${maintenances} maintenance window${maintenances === 1 ? '' : 's'} coming up` : null,
          ]
            .filter(Boolean)
            .join(' · ') || 'No incidents and no maintenance scheduled.'}
        </Alert>
      )}

      {checks.length > 0 && (
        <>
          <Title order={4}>This app</Title>
          <Stack gap="xs">
            {checks.map((c) => (
              <Group key={c.name} wrap="nowrap" align="flex-start" gap="sm">
                <Badge color={CHECK_STATES[c.state].color} w={92} style={{ flexShrink: 0 }}>
                  {CHECK_STATES[c.state].label}
                </Badge>
                <Text size="sm">
                  <b>{c.name}.</b> {c.detail}
                </Text>
              </Group>
            ))}
          </Stack>
        </>
      )}

      {providers.length > 0 && (
        <>
          <Title order={4} mt="sm">
            Outside services
          </Title>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
            {providers.map((p) => (
              <ProviderCard key={p.key} provider={p} />
            ))}
          </SimpleGrid>
        </>
      )}
    </Stack>
  )
}
