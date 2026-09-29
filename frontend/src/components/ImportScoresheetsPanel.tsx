import { useRef, useState } from 'react'
import { Alert, Button, FileButton, Group, Progress, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createGame, extractGameSheet, updateGame } from '../api/games'
import type { ExtractResult, GameData } from '../api/types'
import { ApiError } from '../api/client'
import { GameForm } from './GameForm'
import { GameSheetPreview } from './GameSheetPreview'

interface QueueItem {
  id: string
  label: string
  file: File
  page?: number
  extractedData: GameData
  duplicate: ExtractResult['duplicate']
  status: 'pending' | 'skipped' | 'done'
  replaceTargetId?: number
}

let nextId = 0

export function ImportScoresheetsPanel({ divisionId }: { divisionId: number }) {
  const queryClient = useQueryClient()
  const resetRef = useRef<() => void>(null)
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [extracting, setExtracting] = useState(false)
  const [messages, setMessages] = useState<string[]>([])

  async function handleFiles(files: File[]) {
    if (files.length === 0) return
    setExtracting(true)
    const newItems: QueueItem[] = []
    for (const file of files) {
      try {
        const results = await extractGameSheet(file)
        results.forEach((r, i) => {
          newItems.push({
            id: String(nextId++),
            label: r.label,
            file,
            page: results.length > 1 ? i + 1 : undefined,
            extractedData: r.extracted_data,
            duplicate: r.duplicate,
            status: 'pending',
          })
        })
      } catch (err) {
        notifications.show({
          color: 'red',
          message: err instanceof ApiError ? `${file.name}: ${err.message}` : `${file.name}: extraction failed`,
        })
      }
    }
    setQueue((q) => [...q, ...newItems])
    setExtracting(false)
    resetRef.current?.()
  }

  function updateItem(id: string, patch: Partial<QueueItem>) {
    setQueue((q) => q.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }

  const saveMutation = useMutation({
    mutationFn: async ({ item, data }: { item: QueueItem; data: GameData }) => {
      if (item.replaceTargetId) {
        await updateGame(item.replaceTargetId, data, divisionId)
        return { id: item.replaceTargetId, already_existed: true }
      }
      return createGame(data, item.label, divisionId)
    },
    onSuccess: (result, { item }) => {
      updateItem(item.id, { status: 'done' })
      queryClient.invalidateQueries({ queryKey: ['games', divisionId] })
      queryClient.invalidateQueries({ queryKey: ['schedule', divisionId] })
      const msg = item.replaceTargetId
        ? `Replaced game_id=${result.id} with "${item.label}".`
        : `Saved "${item.label}" as game_id=${result.id}.` + (result.already_existed ? ' (Game already existed — stats were re-inserted.)' : '')
      setMessages((m) => [...m, msg])
    },
    onError: (err) => {
      notifications.show({ color: 'red', message: err instanceof ApiError ? err.message : 'Failed to save game.' })
    },
  })

  const pendingDuplicates = queue.filter((item) => item.status === 'pending' && item.duplicate && item.replaceTargetId === undefined)
  const currentItem = queue.find((item) => item.status === 'pending' && (!item.duplicate || item.replaceTargetId !== undefined))
  const doneCount = queue.filter((item) => item.status !== 'pending').length

  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Upload game sheet scan(s) (PDF or image). A multi-page PDF is split into one item per page automatically.
      </Text>
      <Group>
        <FileButton resetRef={resetRef} onChange={handleFiles} accept=".pdf,.png,.jpg,.jpeg,.webp,.gif" multiple>
          {(props) => (
            <Button {...props} loading={extracting}>
              Upload game sheet(s)
            </Button>
          )}
        </FileButton>
      </Group>

      {queue.length > 0 && (
        <>
          <Progress value={(doneCount / queue.length) * 100} />
          <Text size="sm" c="dimmed">
            {doneCount}/{queue.length} sheets handled
          </Text>
        </>
      )}

      {pendingDuplicates.length > 0 && (
        <Stack gap="xs" p="md" style={{ border: '1px solid var(--mantine-color-yellow-4)', borderRadius: 8 }}>
          <Title order={5}>Duplicates Uploaded</Title>
          <Text size="sm" c="dimmed">
            These files match a game already in the database.
          </Text>
          {pendingDuplicates.map((item) => (
            <Group key={item.id} justify="space-between">
              <Text size="sm">
                "{item.label}" — game_id={item.duplicate!.id}: {item.duplicate!.home_team}{' '}
                {item.duplicate!.home_final_score}–{item.duplicate!.away_final_score} {item.duplicate!.away_team} (
                {item.duplicate!.game_date})
              </Text>
              <Group gap="xs">
                <Button size="xs" onClick={() => updateItem(item.id, { replaceTargetId: item.duplicate!.id })}>
                  Replace
                </Button>
                <Button size="xs" variant="subtle" onClick={() => updateItem(item.id, { status: 'skipped' })}>
                  Skip
                </Button>
              </Group>
            </Group>
          ))}
        </Stack>
      )}

      {!currentItem && queue.length > 0 && pendingDuplicates.length === 0 && (
        <Alert color="green">All uploaded sheets have been handled.</Alert>
      )}

      {currentItem && (
        <Stack>
          <Title order={4}>{currentItem.label}</Title>
          <SimpleGrid cols={2} spacing="md">
            <GameSheetPreview file={currentItem.file} page={currentItem.page} />
            <GameForm
              key={currentItem.id}
              initialData={currentItem.extractedData}
              divisionId={divisionId}
              submitLabel="Accept & Save"
              submitting={saveMutation.isPending}
              onSubmit={(data) => saveMutation.mutate({ item: currentItem, data })}
              extraActions={
                <Button variant="subtle" onClick={() => updateItem(currentItem.id, { status: 'skipped' })}>
                  Skip this sheet
                </Button>
              }
            />
          </SimpleGrid>
        </Stack>
      )}

      {messages.map((m, i) => (
        <Alert key={i} color="blue">
          {m}
        </Alert>
      ))}
    </Stack>
  )
}
