import { useState } from 'react'
import {
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createCoach,
  deleteCoach,
  linkCoachChild,
  listCoachChildren,
  listCoaches,
  listCoachTeams,
  restoreCoach,
  unlinkCoachChild,
  updateCoach,
} from '../api/coaches'
import { listPlayers } from '../api/players'
import { listDivisionPlayers, listDivisions } from '../api/divisions'
import type { Coach } from '../api/types'
import { ApiError } from '../api/client'
import { divisionLabel } from '../utils/format'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { ReadOnlyNotice, useAccess, Writable } from '../auth/access'

export function CoachesPage() {
  const queryClient = useQueryClient()
  const { hideContactDetails, readOnly } = useAccess()
  const { workingDivisionId } = useWorkingDivision()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [newFirst, setNewFirst] = useState('')
  const [newLast, setNewLast] = useState('')
  const [newNickname, setNewNickname] = useState('')
  const [newPhone, setNewPhone] = useState('')
  const [newEmail, setNewEmail] = useState('')

  const { data: coaches } = useQuery({ queryKey: ['coaches'], queryFn: () => listCoaches() })
  const { data: allCoaches } = useQuery({ queryKey: ['coaches', 'all'], queryFn: () => listCoaches(true) })
  const deletedCoaches = (allCoaches ?? []).filter((c) => c.deleted_at)
  const { data: divisions } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data: allPlayers } = useQuery({
    queryKey: ['players'],
    queryFn: () => listPlayers(),
    enabled: workingDivisionId == null,
  })
  const { data: divisionPlayers } = useQuery({
    queryKey: ['division-players', workingDivisionId],
    queryFn: () => listDivisionPlayers(workingDivisionId!),
    enabled: workingDivisionId != null,
  })
  const players = workingDivisionId != null ? divisionPlayers : allPlayers

  const coach = coaches?.find((c) => String(c.id) === selectedId);

  const { data: children } = useQuery({
    queryKey: ['coach-children', coach?.id],
    queryFn: () => listCoachChildren(coach!.id),
    enabled: !!coach,
  })
  const { data: teamsCoached } = useQuery({
    queryKey: ['coach-teams', coach?.id],
    queryFn: () => listCoachTeams(coach!.id),
    enabled: !!coach,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })

  const createMutation = useMutation({
    mutationFn: () =>
      createCoach({
        first_name: newFirst.trim(),
        last_name: newLast.trim() || null,
        nickname: newNickname.trim() || null,
        ...(hideContactDetails ? {} : { phone: newPhone.trim() || null, email: newEmail.trim() || null }),
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['coaches'] })
      setSelectedId(String(created.id))
      setCreateOpen(false)
      setNewFirst('')
      setNewLast('')
      setNewNickname('')
      setNewPhone('')
      setNewEmail('')
    },
    onError,
  })

  const updateMutation = useMutation({
    mutationFn: (fields: Partial<Coach>) => updateCoach(coach!.id, fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['coaches'] })
      notifications.show({ message: 'Saved.' })
    },
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteCoach(coach!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['coaches'] })
      setSelectedId(null)
    },
    onError,
  })

  const restoreMutation = useMutation({
    mutationFn: (coachId: number) => restoreCoach(coachId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['coaches'] }),
    onError,
  })

  const linkChildMutation = useMutation({
    mutationFn: (playerId: number) => linkCoachChild(coach!.id, playerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['coach-children', coach?.id] }),
    onError,
  })

  const unlinkChildMutation = useMutation({
    mutationFn: (playerId: number) => unlinkCoachChild(coach!.id, playerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['coach-children', coach?.id] }),
    onError,
  })

  const [linkPlayerId, setLinkPlayerId] = useState<string | null>(null)
  const linkedChildIds = new Set((children ?? []).map((c) => c.id))
  const pickablePlayers = (players ?? []).filter(
    (p) => !linkedChildIds.has(p.id) && p.name.trim().toLowerCase() !== 'sub'
  )

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Coaches</Title>
        {!readOnly && <Button onClick={() => setCreateOpen(true)}>Add Coach</Button>}
      </Group>
      <Text size="sm" c="dimmed">
        Coach profiles are global — the same coach keeps one profile across every division/season they
        coach in.
      </Text>
      <ReadOnlyNotice />

      <Select
        label="Select a coach"
        placeholder="Choose a coach"
        data={(coaches ?? []).map((c) => ({ value: String(c.id), label: c.name }))}
        value={selectedId}
        onChange={setSelectedId}
        searchable
      />

      {coach && (
        <Writable>
        <Stack mt="md" gap="lg">
          <Group grow>
            <TextInput
              label="First name"
              defaultValue={coach.first_name}
              onBlur={(e) => e.currentTarget.value !== coach.first_name && updateMutation.mutate({ first_name: e.currentTarget.value })}
            />
            <TextInput
              label="Last name"
              defaultValue={coach.last_name ?? ''}
              onBlur={(e) => e.currentTarget.value !== (coach.last_name ?? '') && updateMutation.mutate({ last_name: e.currentTarget.value })}
            />
            <TextInput
              label="Nickname"
              defaultValue={coach.nickname ?? ''}
              onBlur={(e) => e.currentTarget.value !== (coach.nickname ?? '') && updateMutation.mutate({ nickname: e.currentTarget.value })}
            />
          </Group>
          {hideContactDetails ? (
            <Text size="sm" c="dimmed">
              🔒 Phone/email are hidden for your role.
            </Text>
          ) : (
            <Group grow>
              <TextInput
                label="Phone"
                defaultValue={coach.phone ?? ''}
                onBlur={(e) => e.currentTarget.value !== (coach.phone ?? '') && updateMutation.mutate({ phone: e.currentTarget.value })}
              />
              <TextInput
                label="Email"
                defaultValue={coach.email ?? ''}
                onBlur={(e) => e.currentTarget.value !== (coach.email ?? '') && updateMutation.mutate({ email: e.currentTarget.value })}
              />
            </Group>
          )}

          <div>
            <Title order={5}>Children registered</Title>
            {children && children.length > 0 ? (
              <Stack gap={4} mt="xs">
                {children.map((child) => (
                  <Group key={child.id} justify="space-between">
                    <Text size="sm">
                      {child.name}{' '}
                      {child.current_division_id
                        ? `(${divisions?.find((d) => d.id === child.current_division_id)?.year ?? ''})`
                        : ''}
                    </Text>
                    <Button size="compact-xs" variant="subtle" color="red" onClick={() => unlinkChildMutation.mutate(child.id)}>
                      Unlink
                    </Button>
                  </Group>
                ))}
              </Stack>
            ) : (
              <Text size="sm" c="dimmed">
                No registered children linked yet.
              </Text>
            )}
            {workingDivisionId == null && (
              <Text size="xs" c="dimmed" mt="xs">
                Pick a Working Division from the sidebar to filter this list to players registered in it.
              </Text>
            )}
            <Group mt="xs">
              <Select
                placeholder="Link a registered player as this coach's child"
                data={pickablePlayers.map((p) => ({ value: String(p.id), label: p.name }))}
                value={linkPlayerId}
                onChange={setLinkPlayerId}
                searchable
                flex={1}
                nothingFoundMessage={workingDivisionId != null ? 'No registered players in the Working Division.' : undefined}
              />
              <Button
                size="xs"
                disabled={!linkPlayerId}
                onClick={() => linkPlayerId && linkChildMutation.mutate(Number(linkPlayerId))}
              >
                Link
              </Button>
            </Group>
          </div>

          <div>
            <Title order={5}>Teams coached</Title>
            {teamsCoached && teamsCoached.length > 0 ? (
              <Stack gap={2} mt="xs">
                {teamsCoached.map((t, i) => (
                  <Text size="sm" key={i}>
                    {t.year} {t.season} — {divisionLabel(t.age_group, t.category)} ({t.team_name})
                  </Text>
                ))}
              </Stack>
            ) : (
              <Text size="sm" c="dimmed">
                Not assigned to any team yet.
              </Text>
            )}
          </div>

          <Group>
            <Button color="red" variant="outline" onClick={() => confirm(`Delete ${coach.name}?`) && deleteMutation.mutate()}>
              Delete coach
            </Button>
          </Group>
        </Stack>
        </Writable>
      )}

      {!readOnly && deletedCoaches.length > 0 && (
        <>
          <Title order={5} mt="md">
            Deleted Coaches ({deletedCoaches.length})
          </Title>
          <Stack gap="xs">
            {deletedCoaches.map((c) => (
              <Group key={c.id} justify="space-between">
                <Text size="sm">{c.name}</Text>
                <Button size="xs" variant="subtle" onClick={() => restoreMutation.mutate(c.id)}>
                  Restore
                </Button>
              </Group>
            ))}
          </Stack>
        </>
      )}

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="Add a new coach">
        <Stack>
          <TextInput label="First name" value={newFirst} onChange={(e) => setNewFirst(e.currentTarget.value)} required />
          <TextInput label="Last name" value={newLast} onChange={(e) => setNewLast(e.currentTarget.value)} />
          <TextInput label="Nickname" value={newNickname} onChange={(e) => setNewNickname(e.currentTarget.value)} />
          {hideContactDetails ? (
            <Text size="xs" c="dimmed">
              🔒 Phone/email are hidden for your role.
            </Text>
          ) : (
            <Group grow>
              <TextInput label="Phone" value={newPhone} onChange={(e) => setNewPhone(e.currentTarget.value)} />
              <TextInput label="Email" value={newEmail} onChange={(e) => setNewEmail(e.currentTarget.value)} />
            </Group>
          )}
          <Button disabled={!newFirst.trim()} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>
            Add coach
          </Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
