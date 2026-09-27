import { ActionIcon, Button, Group, Select, Table, TextInput } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { Penalty } from '../api/types'

export function PenaltiesEditor({ penalties, onChange }: { penalties: Penalty[]; onChange: (rows: Penalty[]) => void }) {
  function updateRow(i: number, patch: Partial<Penalty>) {
    onChange(penalties.map((p, idx) => (idx === i ? { ...p, ...patch } : p)))
  }
  function removeRow(i: number) {
    onChange(penalties.filter((_, idx) => idx !== i))
  }
  function addRow(side: 'home' | 'away') {
    onChange([...penalties, { side, player_number: '', penalty_type: '', period: null, time: null }])
  }

  return (
    <div>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Side</Table.Th>
            <Table.Th>Player #</Table.Th>
            <Table.Th>Type</Table.Th>
            <Table.Th>Period</Table.Th>
            <Table.Th>Time</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {penalties.map((p, i) => (
            <Table.Tr key={i}>
              <Table.Td>
                <Select
                  size="xs"
                  data={['home', 'away']}
                  value={p.side}
                  onChange={(v) => v && updateRow(i, { side: v as 'home' | 'away' })}
                  allowDeselect={false}
                  w={90}
                />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={p.player_number} onChange={(e) => updateRow(i, { player_number: e.currentTarget.value })} w={80} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={p.penalty_type} onChange={(e) => updateRow(i, { penalty_type: e.currentTarget.value })} w={120} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={p.period ?? ''} onChange={(e) => updateRow(i, { period: e.currentTarget.value || null })} w={70} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={p.time ?? ''} onChange={(e) => updateRow(i, { time: e.currentTarget.value || null })} w={70} />
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
          + Add home penalty
        </Button>
        <Button size="xs" variant="light" onClick={() => addRow('away')}>
          + Add away penalty
        </Button>
      </Group>
    </div>
  )
}
