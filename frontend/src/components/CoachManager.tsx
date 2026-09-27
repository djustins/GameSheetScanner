import { useState } from 'react'
import { Button, Group, Popover, Select, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { assignCoach, listTeamCoaches, removeCoach } from '../api/teams'
import { createCoach, listCoaches } from '../api/coaches'
import { ApiError } from '../api/client'
import { coachLabel } from '../utils/format'

export function CoachManager({ teamId }: { teamId: number }) {
  const queryClient = useQueryClient()
  const [popoverOpen, setPopoverOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [pickCoachId, setPickCoachId] = useState<string | null>(null)
  const [newFirst, setNewFirst] = useState('')
  const [newLast, setNewLast] = useState('')

  const { data: assigned } = useQuery({
    queryKey: ['team-coaches', teamId],
    queryFn: () => listTeamCoaches(teamId),
  })
  const { data: allCoaches } = useQuery({ queryKey: ['coaches'], queryFn: listCoaches })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['team-coaches', teamId] })
  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const assignMutation = useMutation({
    mutationFn: (coachId: number) => assignCoach(teamId, coachId),
    onSuccess: () => {
      invalidate()
      setPopoverOpen(false)
      setSearch('')
      setPickCoachId(null)
    },
    onError,
  })

  const createAndAssignMutation = useMutation({
    mutationFn: async () => {
      const coach = await createCoach({ first_name: newFirst, last_name: newLast || null })
      await assignCoach(teamId, coach.id)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['coaches'] })
      invalidate()
      setPopoverOpen(false)
      setNewFirst('')
      setNewLast('')
    },
    onError,
  })

  const removeMutation = useMutation({
    mutationFn: (coachId: number) => removeCoach(teamId, coachId),
    onSuccess: invalidate,
    onError,
  })

  const assignedIds = new Set((assigned ?? []).map((c) => c.id))
  const matches = (allCoaches ?? [])
    .filter((c) => !assignedIds.has(c.id))
    .filter((c) => {
      if (!search.trim()) return false
      const q = search.trim().toLowerCase()
      return c.name.toLowerCase().includes(q) || (c.nickname ?? '').toLowerCase().includes(q)
    })

  return (
    <Stack gap="xs">
      {assigned && assigned.length > 0 ? (
        <Group gap="xs">
          {assigned.map((c) => (
            <Group key={c.id} gap={4}>
              <Text size="sm">{coachLabel(c)}</Text>
              <Button
                size="compact-xs"
                variant="subtle"
                color="red"
                onClick={() => removeMutation.mutate(c.id)}
              >
                Remove
              </Button>
            </Group>
          ))}
        </Group>
      ) : (
        <Text size="sm" c="dimmed">
          No coaches assigned to this team yet.
        </Text>
      )}

      <Popover opened={popoverOpen} onChange={setPopoverOpen} width={320} withArrow>
        <Popover.Target>
          <Button size="xs" variant="light" onClick={() => setPopoverOpen((o) => !o)}>
            Assign Coach
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap="xs">
            <Text size="sm" fw={500}>
              Search for an existing coach
            </Text>
            <TextInput
              placeholder="Search by name"
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
            />
            {search.trim() && (
              <Group gap="xs">
                <Select
                  data={matches.map((c) => ({ value: String(c.id), label: coachLabel(c) }))}
                  value={pickCoachId}
                  onChange={setPickCoachId}
                  placeholder={matches.length ? 'Choose a match' : 'No matches'}
                  disabled={matches.length === 0}
                  flex={1}
                />
                <Button
                  size="xs"
                  disabled={!pickCoachId}
                  loading={assignMutation.isPending}
                  onClick={() => pickCoachId && assignMutation.mutate(Number(pickCoachId))}
                >
                  Assign
                </Button>
              </Group>
            )}
            <Text size="sm" fw={500} mt="xs">
              Or create a new coach
            </Text>
            <Group gap="xs">
              <TextInput
                placeholder="First name"
                value={newFirst}
                onChange={(e) => setNewFirst(e.currentTarget.value)}
                flex={1}
              />
              <TextInput
                placeholder="Last name"
                value={newLast}
                onChange={(e) => setNewLast(e.currentTarget.value)}
                flex={1}
              />
            </Group>
            <Button
              size="xs"
              disabled={!newFirst.trim()}
              loading={createAndAssignMutation.isPending}
              onClick={() => createAndAssignMutation.mutate()}
            >
              Create & Assign
            </Button>
          </Stack>
        </Popover.Dropdown>
      </Popover>
    </Stack>
  )
}
