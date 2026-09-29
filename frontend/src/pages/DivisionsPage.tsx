import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ActionIcon,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconTrash } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createDivision,
  deleteDivision,
  downloadExportWorkbook,
  listDivisionPlayers,
  listDivisionTeams,
  listDivisions,
} from '../api/divisions'
import { createTeam } from '../api/teams'
import { getAgeGroups } from '../api/meta'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ReadOnlyNotice, useAccess, Writable } from '../auth/access'
import { CoachCarryoverPanel } from '../components/CoachCarryoverPanel'
import { DivisionPlayersTable } from '../components/DivisionPlayersTable'
import { ImportPlayersPanel } from '../components/ImportPlayersPanel'
import { RecycleBinModal } from '../components/RecycleBinModal'
import { SchedulePanel } from '../components/SchedulePanel'
import { TeamsOverviewTable } from '../components/TeamsOverviewTable'
import { divisionSeasonLabel } from '../utils/format'

const SEASONS = ['Spring', 'Summer', 'Fall', 'Winter']

export function DivisionsPage() {
  const { user } = useAuth()
  const { readOnly } = useAccess()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [divisionId, setDivisionId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [recycleBinOpen, setRecycleBinOpen] = useState(false)
  const [newTeamName, setNewTeamName] = useState('')

  const { data: divisions, isLoading } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })
  const { data: ageGroups } = useQuery({ queryKey: ['age-groups'], queryFn: getAgeGroups })

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(Number(divisionId)),
    enabled: divisionId != null,
  })

  const { data: players } = useQuery({
    queryKey: ['division-players', divisionId],
    queryFn: () => listDivisionPlayers(Number(divisionId)),
    enabled: divisionId != null,
  })

  const createMutation = useMutation({
    mutationFn: createDivision,
    onSuccess: (division) => {
      queryClient.invalidateQueries({ queryKey: ['divisions'] })
      setDivisionId(String(division.id))
      setCreateOpen(false)
      form.reset()
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to create division.' })
    },
  })

  const createTeamMutation = useMutation({
    mutationFn: () => createTeam({ division_id: Number(divisionId), name: newTeamName.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['division-teams', divisionId] })
      setNewTeamName('')
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to create team.' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteDivision,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['divisions'] })
      setDivisionId(null)
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to delete division.' })
    },
  })

  const form = useForm({
    initialValues: { year: new Date().getFullYear(), season: 'Fall', age_group: '' },
    validate: {
      season: (v) => (v ? null : 'Required'),
      age_group: (v) => (v ? null : 'Required'),
    },
  })

  const divisionOptions = (divisions ?? []).map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))
  const ageGroupOptions = Object.keys(ageGroups ?? {}).map((ag) => ({ value: ag, label: ag }))
  const selectedDivision = divisions?.find((d) => String(d.id) === divisionId)

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Divisions</Title>
        <Group>
          {!readOnly && (
            <Button variant="subtle" onClick={() => setRecycleBinOpen(true)}>
              Recycle Bin
            </Button>
          )}
          {user?.is_admin && <Button onClick={() => setCreateOpen(true)}>New Division</Button>}
        </Group>
      </Group>

      <Select
        label="Division"
        placeholder="Choose a division"
        data={divisionOptions}
        value={divisionId}
        onChange={setDivisionId}
        disabled={isLoading}
        searchable
        clearable
      />

      {selectedDivision && (
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            {divisionSeasonLabel(selectedDivision)}
          </Text>
          <Group gap="xs">
            <Button size="xs" variant="subtle" onClick={() => downloadExportWorkbook(selectedDivision.id)}>
              Export to Excel
            </Button>
            {user?.is_admin && (
              <ActionIcon
                color="red"
                variant="subtle"
                onClick={() => {
                  if (confirm('Delete this division? Teams and players are kept, just removed from this division.')) {
                    deleteMutation.mutate(selectedDivision.id)
                  }
                }}
              >
                <IconTrash size={18} />
              </ActionIcon>
            )}
          </Group>
        </Group>
      )}

      {divisionId && (
        <Tabs defaultValue="players">
          <Tabs.List>
            <Tabs.Tab value="players">Players</Tabs.Tab>
            <Tabs.Tab value="teams">Teams</Tabs.Tab>
            <Tabs.Tab value="coaches">Coach Carryover</Tabs.Tab>
            <Tabs.Tab value="schedule">Schedule</Tabs.Tab>
            <Tabs.Tab value="import">Import Players</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="players" pt="md">
            <DivisionPlayersTable divisionId={Number(divisionId)} />
          </Tabs.Panel>

          <Tabs.Panel value="teams" pt="md">
            {user?.is_admin && (
              <Group mb="sm">
                <TextInput
                  placeholder="Team name"
                  value={newTeamName}
                  onChange={(e) => setNewTeamName(e.currentTarget.value)}
                />
                <Button
                  disabled={!newTeamName.trim()}
                  loading={createTeamMutation.isPending}
                  onClick={() => createTeamMutation.mutate()}
                >
                  New Team
                </Button>
              </Group>
            )}
            <TeamsOverviewTable
              divisionId={Number(divisionId)}
              teams={teams ?? []}
              onRowClick={(t) => navigate(`/teams/${t.id}?division=${divisionId}`)}
            />
          </Tabs.Panel>

          <Tabs.Panel value="coaches" pt="md">
            <ReadOnlyNotice />
            <Writable>
              <CoachCarryoverPanel divisionId={Number(divisionId)} teams={teams ?? []} divisionPlayers={players ?? []} />
            </Writable>
          </Tabs.Panel>

          <Tabs.Panel value="schedule" pt="md">
            <SchedulePanel divisionId={Number(divisionId)} />
          </Tabs.Panel>

          <Tabs.Panel value="import" pt="md">
            <ReadOnlyNotice />
            <Writable>
              <ImportPlayersPanel divisionId={Number(divisionId)} />
            </Writable>
          </Tabs.Panel>
        </Tabs>
      )}

      <RecycleBinModal opened={recycleBinOpen} onClose={() => setRecycleBinOpen(false)} />

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="New Division">
        <form onSubmit={form.onSubmit((values) => createMutation.mutate(values))}>
          <Stack>
            <NumberInput label="Year" {...form.getInputProps('year')} min={2000} max={2100} required />
            <Select label="Season" data={SEASONS} {...form.getInputProps('season')} required />
            <Select
              label="Age Group"
              data={ageGroupOptions}
              searchable
              {...form.getInputProps('age_group')}
              required
            />
            <Button type="submit" loading={createMutation.isPending}>
              Create
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  )
}
