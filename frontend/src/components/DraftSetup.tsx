import { useState } from 'react'
import {
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQueries } from '@tanstack/react-query'
import { startDraft, updateDraftSettings } from '../api/draft'
import { listTeamCoaches } from '../api/teams'
import { ApiError } from '../api/client'
import type { Draft, DraftSettings, Team } from '../api/types'

const DEFAULT_DRAFT_SETTINGS: DraftSettings = {
  order_type: 'snake',
  rounds: null,
  pick_seconds: null,
  who_picks: 'coaches',
}

const CLOCK_OPTIONS = [
  { value: '', label: 'No clock' },
  { value: '30', label: '30 seconds' },
  { value: '60', label: '1 minute' },
  { value: '90', label: '90 seconds' },
  { value: '120', label: '2 minutes' },
  { value: '300', label: '5 minutes' },
]

const onError = (err: unknown) =>
  notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

/** The settings one draft runs under. `lockOrder` once picks exist: the order
 * type decides whose pick each earlier one was, so it can't change then. A mock
 * has no "who picks" -- anyone practising picks for every team. */
export function DraftSettingsFields({
  value,
  onChange,
  mock,
  lockOrder = false,
}: {
  value: DraftSettings
  onChange: (next: DraftSettings) => void
  mock: boolean
  lockOrder?: boolean
}) {
  const clock = value.pick_seconds == null ? '' : String(value.pick_seconds)
  return (
    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" ta="left">
      <div>
        <Text size="sm" fw={500} mb={4}>
          Order
        </Text>
        <SegmentedControl
          fullWidth
          disabled={lockOrder}
          value={value.order_type}
          onChange={(v) => onChange({ ...value, order_type: v as DraftSettings['order_type'] })}
          data={[
            { value: 'snake', label: 'Snake' },
            { value: 'linear', label: 'Linear' },
          ]}
        />
        <Text size="xs" c="dimmed" mt={4}>
          {lockOrder
            ? "Can't change once picks have been made."
            : value.order_type === 'snake'
              ? 'Each round reverses: whoever picks last in round 1 picks first in round 2.'
              : 'Every round runs in the same order.'}
        </Text>
      </div>
      <NumberInput
        label="Rounds"
        description="Leave blank to draft until the pool is empty."
        placeholder="Until the pool is empty"
        min={1}
        max={99}
        allowDecimal={false}
        value={value.rounds ?? ''}
        onChange={(v) => onChange({ ...value, rounds: typeof v === 'number' ? v : null })}
      />
      <Select
        label="Pick clock"
        description="A countdown everyone sees. Nothing is picked automatically when it runs out."
        data={
          CLOCK_OPTIONS.some((o) => o.value === clock)
            ? CLOCK_OPTIONS
            : [...CLOCK_OPTIONS, { value: clock, label: `${clock} seconds` }]
        }
        value={clock}
        onChange={(v) => onChange({ ...value, pick_seconds: v ? Number(v) : null })}
        allowDeselect={false}
      />
      {!mock && (
        <Select
          label="Who makes picks"
          description="Admins can always pick for any team."
          data={[
            { value: 'coaches', label: "Each team's own coach" },
            { value: 'admins', label: 'Admins only' },
          ]}
          value={value.who_picks}
          onChange={(v) => v && onChange({ ...value, who_picks: v as DraftSettings['who_picks'] })}
          allowDeselect={false}
        />
      )}
    </SimpleGrid>
  )
}

/** Before a draft: choose the team order and this draft's settings, then start
 * it -- the real draft, or a mock one that changes no roster. */
export function DraftSetup({
  divisionId,
  teams,
  mock,
  onStarted,
}: {
  divisionId: number
  teams: Team[]
  mock: boolean
  onStarted: () => void
}) {
  const [order, setOrder] = useState<string[]>([])
  const [settings, setSettings] = useState<DraftSettings>(DEFAULT_DRAFT_SETTINGS)

  const coachQueries = useQueries({
    queries: teams.map((t) => ({ queryKey: ['team-coaches', t.id], queryFn: () => listTeamCoaches(t.id) })),
  })
  const teamLabel = (team: Team, index: number) => {
    const coaches = coachQueries[index]?.data ?? []
    return coaches.length ? `${team.name} (${coaches.map((c) => c.name).join(', ')})` : team.name
  }
  const missing = teams.filter((t) => !order.includes(String(t.id)))

  function randomize() {
    const ids = teams.map((t) => String(t.id))
    for (let i = ids.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[ids[i], ids[j]] = [ids[j], ids[i]]
    }
    setOrder(ids)
  }

  const startMutation = useMutation({
    mutationFn: () => startDraft(divisionId, order.map(Number), { mock, settings }),
    onSuccess: () => {
      setOrder([])
      onStarted()
    },
    onError,
  })

  return (
    <Stack>
      <Group align="flex-end">
        <MultiSelect
          flex={1}
          label={`Draft order — pick teams in the order they should draft${
            settings.order_type === 'snake' ? ' (round 1; later rounds snake back)' : ''
          }`}
          data={teams.map((t, i) => ({ value: String(t.id), label: teamLabel(t, i) }))}
          value={order}
          onChange={setOrder}
        />
        <Button variant="default" onClick={randomize}>
          🔀 Randomize
        </Button>
      </Group>
      {missing.length > 0 && (
        <Text size="sm" c="dimmed" ta="left">
          Still need to add: {missing.map((t) => t.name).join(', ')}
        </Text>
      )}
      <DraftSettingsFields value={settings} onChange={setSettings} mock={mock} />
      <Button
        w="fit-content"
        disabled={missing.length > 0}
        loading={startMutation.isPending}
        onClick={() => startMutation.mutate()}
      >
        {mock ? 'Start mock draft' : 'Start draft'}
      </Button>
    </Stack>
  )
}

/** Change a running draft's settings. */
export function DraftSettingsModal({
  draft,
  picksMade,
  opened,
  onClose,
  onSaved,
}: {
  draft: Draft
  picksMade: number
  opened: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [settings, setSettings] = useState<DraftSettings>(draft.settings)
  const saveMutation = useMutation({
    mutationFn: () => updateDraftSettings(draft.id, settings),
    onSuccess: () => {
      onSaved()
      onClose()
    },
    onError,
  })
  return (
    <Modal opened={opened} onClose={onClose} title={draft.is_mock ? 'Mock draft settings' : 'Draft settings'} size="lg">
      <Stack>
        <DraftSettingsFields value={settings} onChange={setSettings} mock={draft.is_mock} lockOrder={picksMade > 0} />
        <Text size="xs" c="dimmed">
          Changes apply to this draft only, from the next pick on. Setting fewer rounds than have been played ends the
          draft; raising them reopens one that ran out of rounds.
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            Save settings
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
