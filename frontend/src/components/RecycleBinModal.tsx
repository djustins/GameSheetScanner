import { Button, Group, Modal, Stack, Text, Title } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listDeletedDivisions, restoreDivision } from '../api/divisions'
import { listDeletedTeams, restoreTeam } from '../api/teams'
import { divisionSeasonLabel } from '../utils/format'

export function RecycleBinModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()

  const { data: deletedDivisions } = useQuery({
    queryKey: ['deleted-divisions'],
    queryFn: listDeletedDivisions,
    enabled: opened,
  })
  const { data: deletedTeams } = useQuery({
    queryKey: ['deleted-teams'],
    queryFn: listDeletedTeams,
    enabled: opened,
  })

  const restoreDivisionMutation = useMutation({
    mutationFn: restoreDivision,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deleted-divisions'] })
      queryClient.invalidateQueries({ queryKey: ['divisions'] })
    },
  })

  const restoreTeamMutation = useMutation({
    mutationFn: restoreTeam,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deleted-teams'] })
      queryClient.invalidateQueries({ queryKey: ['division-teams'] })
    },
  })

  return (
    <Modal opened={opened} onClose={onClose} title="Recycle Bin" size="md">
      <Stack>
        <Title order={5}>Deleted Divisions</Title>
        {!deletedDivisions || deletedDivisions.length === 0 ? (
          <Text size="sm" c="dimmed">
            None.
          </Text>
        ) : (
          deletedDivisions.map((d) => (
            <Group key={d.id} justify="space-between">
              <Text size="sm">{divisionSeasonLabel(d)}</Text>
              <Button size="xs" variant="subtle" onClick={() => restoreDivisionMutation.mutate(d.id)}>
                Restore
              </Button>
            </Group>
          ))
        )}

        <Title order={5} mt="md">
          Deleted Teams
        </Title>
        {!deletedTeams || deletedTeams.length === 0 ? (
          <Text size="sm" c="dimmed">
            None.
          </Text>
        ) : (
          deletedTeams.map((t) => (
            <Group key={t.id} justify="space-between">
              <Text size="sm">
                {t.name} ({t.year} {t.season} — {t.age_group})
              </Text>
              <Button size="xs" variant="subtle" onClick={() => restoreTeamMutation.mutate(t.id)}>
                Restore
              </Button>
            </Group>
          ))
        )}
      </Stack>
    </Modal>
  )
}
