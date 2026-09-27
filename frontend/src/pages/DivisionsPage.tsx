import { useState } from 'react'
import {
  ActionIcon,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
} from '@mantine/core'
import { useForm } from '@mantine/form'
import { notifications } from '@mantine/notifications'
import { IconTrash } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createDivision,
  deleteDivision,
  listDivisionPlayers,
  listDivisionTeams,
  listDivisions,
} from '../api/divisions'
import { getAgeGroups } from '../api/meta'
import type { Player } from '../api/types'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ExperienceBadge } from '../components/ExperienceBadge'
import { PlayerDetailDrawer } from '../components/PlayerDetailDrawer'
import { divisionSeasonLabel } from '../utils/format'

const SEASONS = ['Spring', 'Summer', 'Fall', 'Winter']

export function DivisionsPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [divisionId, setDivisionId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null)

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
        {user?.is_admin && <Button onClick={() => setCreateOpen(true)}>New Division</Button>}
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
      )}

      {divisionId && (
        <Tabs defaultValue="players">
          <Tabs.List>
            <Tabs.Tab value="players">Players</Tabs.Tab>
            <Tabs.Tab value="teams">Teams</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="players" pt="md">
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Teams</Table.Th>
                  <Table.Th>Grade</Table.Th>
                  <Table.Th>Note</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(players ?? []).map((p) => (
                  <Table.Tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedPlayer(p)}>
                    <Table.Td>{p.name}</Table.Td>
                    <Table.Td>{p.teams.join(', ') || '—'}</Table.Td>
                    <Table.Td>{p.grade ?? '—'}</Table.Td>
                    <Table.Td>
                      <ExperienceBadge note={p.note} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {players?.length === 0 && (
              <Text c="dimmed" size="sm" mt="sm">
                No players in this division yet.
              </Text>
            )}
          </Tabs.Panel>

          <Tabs.Panel value="teams" pt="md">
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Color</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(teams ?? []).map((t) => (
                  <Table.Tr key={t.id}>
                    <Table.Td>{t.name}</Table.Td>
                    <Table.Td>{t.color ?? '—'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Tabs.Panel>
        </Tabs>
      )}

      <PlayerDetailDrawer player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />

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
