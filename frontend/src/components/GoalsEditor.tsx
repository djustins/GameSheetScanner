import { ActionIcon, Button, Group, Select, Table, TextInput } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { Goal } from '../api/types'

export function GoalsEditor({ goals, onChange }: { goals: Goal[]; onChange: (goals: Goal[]) => void }) {
  function updateRow(i: number, patch: Partial<Goal>) {
    onChange(goals.map((g, idx) => (idx === i ? { ...g, ...patch } : g)))
  }
  function removeRow(i: number) {
    onChange(goals.filter((_, idx) => idx !== i))
  }
  function addRow(side: 'home' | 'away') {
    onChange([...goals, { side, scorer_number: '', assist1_number: null, assist2_number: null, period: null, time: null }])
  }

  return (
    <div>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Side</Table.Th>
            <Table.Th>Scorer #</Table.Th>
            <Table.Th>Assist 1</Table.Th>
            <Table.Th>Assist 2</Table.Th>
            <Table.Th>Period</Table.Th>
            <Table.Th>Time</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {goals.map((g, i) => (
            <Table.Tr key={i}>
              <Table.Td>
                <Select
                  size="xs"
                  data={['home', 'away']}
                  value={g.side}
                  onChange={(v) => v && updateRow(i, { side: v as 'home' | 'away' })}
                  allowDeselect={false}
                  w={90}
                />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={g.scorer_number} onChange={(e) => updateRow(i, { scorer_number: e.currentTarget.value })} w={80} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={g.assist1_number ?? ''} onChange={(e) => updateRow(i, { assist1_number: e.currentTarget.value || null })} w={80} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={g.assist2_number ?? ''} onChange={(e) => updateRow(i, { assist2_number: e.currentTarget.value || null })} w={80} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={g.period ?? ''} onChange={(e) => updateRow(i, { period: e.currentTarget.value || null })} w={70} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={g.time ?? ''} onChange={(e) => updateRow(i, { time: e.currentTarget.value || null })} w={70} />
              </Table.Td>
              <Table.Td>
                <ActionIcon color="red" variant="subtle" onClick={() => removeRow(i)}>
                  <IconTrash size={14} />
                </ActionIcon>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group mt="xs">
        <Button size="xs" variant="light" onClick={() => addRow('home')}>
          + Add home goal
        </Button>
        <Button size="xs" variant="light" onClick={() => addRow('away')}>
          + Add away goal
        </Button>
      </Group>
    </div>
  )
}
