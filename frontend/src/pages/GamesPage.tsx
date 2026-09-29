import { useState } from 'react'
import { Alert, Stack, Tabs, Title } from '@mantine/core'
import { ImportScoresheetsPanel } from '../components/ImportScoresheetsPanel'
import { ManageGamesPanel } from '../components/ManageGamesPanel'
import { ScheduleResultsPanel } from '../components/ScheduleResultsPanel'
import { useWorkingDivision } from '../context/WorkingDivisionContext'
import { ReadOnlyNotice, useAccess, Writable } from '../auth/access'

export function GamesPage() {
  const { workingDivisionId } = useWorkingDivision()
  const { canView } = useAccess()
  // Tab values match game_sheet_core.PAGES keys, so each is gated directly.
  const visibleTabs = ['schedule', 'process', 'edit'].filter(canView)
  const [tab, setTab] = useState<string | null>(visibleTabs[0] ?? null)

  return (
    <Stack>
      <Title order={2}>Games</Title>

      {workingDivisionId == null ? (
        <Alert color="yellow">Pick a Working Division from the sidebar first.</Alert>
      ) : (
        <Tabs value={tab} onChange={setTab}>
          <Tabs.List>
            {canView('schedule') && <Tabs.Tab value="schedule">Schedule &amp; Results</Tabs.Tab>}
            {canView('process') && <Tabs.Tab value="process">Import Scoresheets</Tabs.Tab>}
            {canView('edit') && <Tabs.Tab value="edit">Manage Games</Tabs.Tab>}
          </Tabs.List>

          {canView('schedule') && (
            <Tabs.Panel value="schedule" pt="md">
              <ScheduleResultsPanel
                divisionId={workingDivisionId}
                onImportScoresheets={canView('process') ? () => setTab('process') : undefined}
              />
            </Tabs.Panel>
          )}

          {canView('process') && (
            <Tabs.Panel value="process" pt="md">
              <ReadOnlyNotice />
              <Writable>
                <ImportScoresheetsPanel divisionId={workingDivisionId} />
              </Writable>
            </Tabs.Panel>
          )}

          {canView('edit') && (
            <Tabs.Panel value="edit" pt="md">
              <ReadOnlyNotice />
              <Writable>
                <ManageGamesPanel divisionId={workingDivisionId} />
              </Writable>
            </Tabs.Panel>
          )}
        </Tabs>
      )}
    </Stack>
  )
}
