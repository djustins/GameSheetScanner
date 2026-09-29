import { useState } from 'react'
import { Accordion, Button, Stack, Table, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createGame, deleteGame, getGame, listGames, updateGame } from '../api/games'
import type { GameData } from '../api/types'
import { ApiError } from '../api/client'
import { GameForm } from './GameForm'

export function ManageGamesPanel({ divisionId }: { divisionId: number }) {
  const queryClient = useQueryClient()
  const [editGameId, setEditGameId] = useState<number | null>(null);
  const [addOpen, setAddOpen] = useState(false)

  const { data: games } = useQuery({ queryKey: ['games', divisionId], queryFn: () => listGames(divisionId) })
  const { data: editing } = useQuery({
    queryKey: ['game', editGameId],
    queryFn: () => getGame(editGameId!),
    enabled: editGameId != null,
  })

  const onError = (err: unknown) =>
    notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Something went wrong.' })
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['games', divisionId] })
    // Schedule & Results derives each row's result from stored games.
    queryClient.invalidateQueries({ queryKey: ['schedule', divisionId] })
  }

  const addMutation = useMutation({
    mutationFn: (data: GameData) => createGame(data, '(manual entry)', divisionId),
    onSuccess: (result) => {
      invalidate()
      setAddOpen(false)
      notifications.show({
        message: `Added game_id=${result.id} manually.` + (result.already_existed ? ' (Matching game already existed — stats were re-inserted.)' : ''),
      })
    },
    onError,
  })

  const editMutation = useMutation({
    mutationFn: (data: GameData) => updateGame(editGameId!, data, divisionId),
    onSuccess: () => {
      invalidate()
      notifications.show({ message: `Updated game_id=${editGameId}.` })
    },
    onError,
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteGame(editGameId!),
    onSuccess: () => {
      invalidate()
      setEditGameId(null)
    },
    onError,
  })

  return (
    <Stack>
      <Button size="xs" variant="light" onClick={() => setAddOpen((o) => !o)} w={220}>
        {addOpen ? 'Cancel manual add' : '+ Add Game Manually'}
      </Button>
      {addOpen && (
        <GameForm
          key="add-new"
          divisionId={divisionId}
          submitLabel="Save new game"
          submitting={addMutation.isPending}
          onSubmit={(data) => addMutation.mutate(data)}
        />
      )}

      <Title order={4} mt="md">
        Games
      </Title>
      {!games || games.length === 0 ? (
        <Text size="sm" c="dimmed">
          No games in the database yet.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Date</Table.Th>
              <Table.Th>Home</Table.Th>
              <Table.Th>Away</Table.Th>
              <Table.Th>Score</Table.Th>
              <Table.Th>Source</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {games.map((g) => (
              <Table.Tr key={g.id} style={{ cursor: 'pointer' }} onClick={() => setEditGameId(g.id)}>
                <Table.Td>{g.game_date}</Table.Td>
                <Table.Td>{g.home_team}</Table.Td>
                <Table.Td>{g.away_team}</Table.Td>
                <Table.Td>
                  {g.home_final_score}–{g.away_final_score}
                </Table.Td>
                <Table.Td>{g.source_file}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {editGameId != null && editing && (
        <Accordion variant="separated" defaultValue="edit">
          <Accordion.Item value="edit">
            <Accordion.Control>Editing game_id={editGameId}</Accordion.Control>
            <Accordion.Panel>
              <GameForm
                key={editGameId}
                initialData={editing.data}
                divisionId={divisionId}
                submitLabel="Save changes"
                submitting={editMutation.isPending}
                onSubmit={(data) => editMutation.mutate(data)}
                extraActions={
                  <Button
                    color="red"
                    variant="outline"
                    onClick={() =>
                      confirm(`Permanently delete game_id=${editGameId}? This can't be undone.`) &&
                      deleteMutation.mutate()
                    }
                  >
                    Delete this game
                  </Button>
                }
              />
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Stack>
  )
}
