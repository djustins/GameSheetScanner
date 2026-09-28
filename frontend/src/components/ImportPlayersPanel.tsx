import { useRef, useState } from 'react'
import { Alert, Badge, Button, FileButton, Group, Select, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { applyImportPlan, getImportPlan } from '../api/playerImport'
import type { PlayerImportEntry, PlayerImportResult } from '../api/types'
import { ApiError } from '../api/client'

const STATUS_LABELS: Record<string, string> = {
  create: 'new',
  update: 'matched to an existing profile',
  ambiguous: 'ambiguous',
  conflict: 'need review (birth date mismatch)',
  invalid: 'skipped (no name)',
}

export function ImportPlayersPanel({ divisionId }: { divisionId: number }) {
  const queryClient = useQueryClient()
  const resetRef = useRef<() => void>(null)
  const [plan, setPlan] = useState<PlayerImportEntry[] | null>(null)
  const [result, setResult] = useState<PlayerImportResult | null>(null)

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const planMutation = useMutation({
    mutationFn: (file: File) => getImportPlan(divisionId, file),
    onSuccess: (response) => {
      setPlan(response.plan)
      setResult(null)
      resetRef.current?.()
    },
    onError,
  })

  const applyMutation = useMutation({
    mutationFn: () => applyImportPlan(divisionId, plan!),
    onSuccess: (r) => {
      setResult(r)
      setPlan(null)
      queryClient.invalidateQueries({ queryKey: ['division-players', String(divisionId)] })
      queryClient.invalidateQueries({ queryKey: ['division-teams', String(divisionId)] })
      queryClient.invalidateQueries({ queryKey: ['players'] })
    },
    onError,
  })

  function handleFile(file: File | null) {
    if (!file) return
    planMutation.mutate(file)
  }

  function updateEntry(rowNumber: number, patch: Partial<PlayerImportEntry>) {
    setPlan((p) => (p ? p.map((e) => (e.row_number === rowNumber ? { ...e, ...patch } : e)) : p))
  }

  if (!plan) {
    return (
      <Stack>
        <Text size="sm" c="dimmed">
          Upload a player list (CSV, Excel, or ODS) for this division. Matches existing player profiles by
          name — using birth date too, when given, to tell same-named players apart or flag a possible
          mismatch — and creates new profiles otherwise. A Team column also assigns each player to that
          team&apos;s roster (with a jersey number, if given); a Coach column assigns that coach to the team
          too.
        </Text>
        <Group>
          <FileButton resetRef={resetRef} onChange={handleFile} accept=".csv,.xlsx,.xls,.ods">
            {(props) => (
              <Button {...props} loading={planMutation.isPending}>
                Upload player list
              </Button>
            )}
          </FileButton>
        </Group>
        {result && (
          <Alert color="green" title="Import complete">
            Created {result.created}, updated {result.updated}, added to a roster {result.rostered}, assigned{' '}
            {result.coached} coach(es), skipped {result.skipped}.
            {result.warnings.map((w, i) => (
              <Text size="sm" key={i}>
                ⚠️ {w}
              </Text>
            ))}
          </Alert>
        )}
      </Stack>
    )
  }

  const counts: Record<string, number> = {}
  for (const e of plan) counts[e.status] = (counts[e.status] ?? 0) + 1
  const summary = Object.entries(counts)
    .map(([status, n]) => `${n} ${STATUS_LABELS[status] ?? status}`)
    .join(', ')

  const needsReview = plan.filter((e) => e.status === 'ambiguous' || e.status === 'conflict')
  const stillUnresolved = needsReview.filter((e) => e.resolved_action == null).length

  return (
    <Stack>
      <Alert color="blue">
        {plan.length} row(s) in file: {summary}.
      </Alert>

      {needsReview.length > 0 && (
        <Stack gap="md" p="md" style={{ border: '1px solid var(--mantine-color-yellow-4)', borderRadius: 8 }}>
          <Title order={5}>{needsReview.length} row(s) need your input before they&apos;ll be imported</Title>
          {needsReview.map((entry) => {
            const options: { value: string; label: string }[] = [{ value: '__unresolved__', label: '— Choose one —' }]
            if (entry.status === 'conflict' && entry.conflict_detail) {
              const d = entry.conflict_detail
              options.push({
                value: 'use_existing',
                label: `Same person — update the existing profile (birth date ${d.existing} → ${d.incoming})`,
              })
              options.push({ value: 'create', label: 'Different person — create a new profile' })
            } else {
              for (const c of entry.candidates) {
                options.push({ value: `use:${c.id}`, label: `${c.name} (born ${c.birth_date ?? 'unknown'})` })
              }
              options.push({ value: 'create', label: 'None of these — create a new profile' })
            }

            const currentValue =
              entry.resolved_action == null
                ? '__unresolved__'
                : entry.resolved_action === 'use_existing'
                  ? entry.status === 'conflict'
                    ? 'use_existing'
                    : `use:${entry.resolved_player_id}`
                  : 'create'

            return (
              <div key={entry.row_number}>
                <Text fw={700} size="sm">
                  Row {entry.row_number}: {entry.name}
                </Text>
                {entry.status === 'conflict' && entry.conflict_detail?.sibling_match && (
                  <Alert color="red" mb="xs">
                    ⚠️ This birth date ({entry.conflict_detail.incoming}) matches{' '}
                    <strong>{entry.conflict_detail.sibling_match}</strong>&apos;s (a sibling) on file — this
                    looks like two kids&apos; dates got swapped in the file, not a new person. Double-check
                    before picking &quot;Different person&quot;.
                  </Alert>
                )}
                <Select
                  data={options}
                  value={currentValue}
                  onChange={(v) => {
                    if (!v || v === '__unresolved__') {
                      updateEntry(entry.row_number, { resolved_action: null, resolved_player_id: null })
                    } else if (v === 'create') {
                      updateEntry(entry.row_number, { resolved_action: 'create', resolved_player_id: null })
                    } else if (v === 'use_existing') {
                      updateEntry(entry.row_number, {
                        resolved_action: 'use_existing',
                        resolved_player_id: entry.matched_player_id,
                      })
                    } else if (v.startsWith('use:')) {
                      updateEntry(entry.row_number, {
                        resolved_action: 'use_existing',
                        resolved_player_id: Number(v.slice(4)),
                      })
                    }
                  }}
                />
              </div>
            )
          })}
          {stillUnresolved > 0 && (
            <Text size="sm" c="dimmed">
              {stillUnresolved} row(s) above still need a choice — they&apos;ll be skipped (not guessed at) if
              you import now.
            </Text>
          )}
        </Stack>
      )}

      <Group>
        <Button loading={applyMutation.isPending} onClick={() => applyMutation.mutate()}>
          Apply Import
        </Button>
        <Button variant="subtle" onClick={() => setPlan(null)}>
          Cancel
        </Button>
      </Group>

      <Stack gap={4}>
        {plan.map((e) => (
          <Group key={e.row_number} gap="xs">
            <Badge
              color={
                e.status === 'create'
                  ? 'green'
                  : e.status === 'update'
                    ? 'blue'
                    : e.status === 'invalid'
                      ? 'gray'
                      : 'yellow'
              }
              variant="light"
            >
              {e.status}
            </Badge>
            <Text size="sm">
              Row {e.row_number}: {e.name ?? '(no name)'}
            </Text>
          </Group>
        ))}
      </Stack>
    </Stack>
  )
}
