import { Button, Group, Select, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getSyncSchedule, setSyncSchedule, syncLeagueSite } from '../api/divisions'
import { ApiError } from '../api/client'

const FREQUENCIES = [
  { value: '1', label: 'Every hour' },
  { value: '2', label: 'Every 2 hours' },
  { value: '3', label: 'Every 3 hours' },
  { value: '4', label: 'Every 4 hours' },
  { value: '6', label: 'Every 6 hours' },
  { value: '12', label: 'Every 12 hours' },
  { value: '24', label: 'Once a day' },
  { value: '0', label: 'Off' },
]

const HOURS = Array.from({ length: 24 }, (_, h) => ({
  value: String(h),
  label: `${h % 12 === 0 ? 12 : h % 12}:15 ${h < 12 ? 'am' : 'pm'}`,
}))

const onError = (err: unknown) =>
  notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

/** Admin-only: how often games and stats are pulled in from the league stats
 * site, and a button to do it right now. */
export function LeagueSyncSettings() {
  const queryClient = useQueryClient()
  const { data: schedule } = useQuery({ queryKey: ['sync-schedule'], queryFn: getSyncSchedule })

  const saveMutation = useMutation({
    mutationFn: setSyncSchedule,
    onSuccess: (saved) => {
      queryClient.setQueryData(['sync-schedule'], saved)
      notifications.show({ color: 'green', message: 'Sync schedule saved.' })
    },
    onError,
  })
  const syncMutation = useMutation({
    mutationFn: syncLeagueSite,
    onSuccess: (reports) => {
      const added = reports.reduce((n, r) => n + (r.added?.length ?? 0), 0)
      const failed = reports.reduce((n, r) => n + (r.failed?.length ?? 0), 0)
      notifications.show({
        color: failed ? 'orange' : 'green',
        autoClose: failed ? false : 5000,
        message: `Synced: ${added} new game${added === 1 ? '' : 's'}${failed ? `, ${failed} could not be saved` : ''}.`,
      })
      for (const key of [['league-sync'], ['stats'], ['standings'], ['schedule'], ['games'], ['roster']]) {
        queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError,
  })

  return (
    <Stack>
      <div>
        <Title order={4}>League site sync</Title>
        <Text size="sm" c="dimmed">
          How often games, scores and stats are pulled in from the league stats site. It checks at a quarter past the
          hour, give or take a few minutes.
        </Text>
      </div>
      <Group align="flex-end">
        <Select
          label="Sync automatically"
          w={200}
          data={FREQUENCIES}
          value={schedule ? String(schedule.every_hours) : null}
          onChange={(v) => v != null && saveMutation.mutate({ every_hours: Number(v) })}
          allowDeselect={false}
          disabled={!schedule || saveMutation.isPending}
        />
        {schedule?.every_hours === 24 && (
          <Select
            label="At (Pittsburgh time)"
            w={160}
            data={HOURS}
            value={String(schedule.daily_hour)}
            onChange={(v) => v != null && saveMutation.mutate({ every_hours: 24, daily_hour: Number(v) })}
            allowDeselect={false}
            disabled={saveMutation.isPending}
          />
        )}
        <Button variant="default" loading={syncMutation.isPending} onClick={() => syncMutation.mutate()}>
          Sync now
        </Button>
      </Group>
      <Text size="sm" c="dimmed">
        {schedule?.last_run
          ? `Last automatic sync: ${new Date(schedule.last_run).toLocaleString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
            })}.`
          : 'No automatic sync has run yet.'}
      </Text>
    </Stack>
  )
}
