import { useState } from 'react'
import { Select, Stack, Tabs, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listDivisions } from '../api/divisions'
import { ImportScoresheetsPanel } from '../components/ImportScoresheetsPanel'
import { ManageGamesPanel } from '../components/ManageGamesPanel'
import { divisionSeasonLabel } from '../utils/format'

export function GamesPage() {
  const [divisionId, setDivisionId] = useState<string | null>(null)
  const { data: divisions, isLoading } = useQuery({ queryKey: ['divisions'], queryFn: listDivisions })

  const divisionOptions = (divisions ?? []).map((d) => ({ value: String(d.id), label: divisionSeasonLabel(d) }))

  return (
    <Stack>
      <Title order={2}>Games</Title>
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

      {divisionId && (
        <Tabs defaultValue="import">
          <Tabs.List>
            <Tabs.Tab value="import">Import Scoresheets</Tabs.Tab>
            <Tabs.Tab value="manage">Manage Games</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="import" pt="md">
            <ImportScoresheetsPanel divisionId={Number(divisionId)} />
          </Tabs.Panel>

          <Tabs.Panel value="manage" pt="md">
            <ManageGamesPanel divisionId={Number(divisionId)} />
          </Tabs.Panel>
        </Tabs>
      )}
    </Stack>
  )
}
