import { ActionIcon, Button, Checkbox, Group, NumberInput, Select, Table, TextInput } from '@mantine/core'
import { IconTrash } from '@tabler/icons-react'
import type { ShootoutAttempt } from '../api/types'

export function ShootoutEditor({ attempts, onChange }: { attempts: ShootoutAttempt[]; onChange: (rows: ShootoutAttempt[]) => void }) {
  function updateRow(i: number, patch: Partial<ShootoutAttempt>) {
    onChange(attempts.map((a, idx) => (idx === i ? { ...a, ...patch } : a)))
  }
  function removeRow(i: number) {
    onChange(attempts.filter((_, idx) => idx !== i))
  }
  function addRow(side: 'home' | 'away') {
    const round = attempts.filter((a) => a.side === side).length + 1
    onChange([...attempts, { side, round, player_number: '', scored: false }])
  }

  return (
    <div>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Side</Table.Th>
            <Table.Th>Round</Table.Th>
            <Table.Th>Player #</Table.Th>
            <Table.Th>Scored</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {attempts.map((a, i) => (
            <Table.Tr key={i}>
              <Table.Td>
                <Select
                  size="xs"
                  data={['home', 'away']}
                  value={a.side}
                  onChange={(v) => v && updateRow(i, { side: v as 'home' | 'away' })}
                  allowDeselect={false}
                  w={90}
                />
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" value={a.round} onChange={(v) => updateRow(i, { round: Number(v) || 1 })} min={1} w={70} />
              </Table.Td>
              <Table.Td>
                <TextInput size="xs" value={a.player_number} onChange={(e) => updateRow(i, { player_number: e.currentTarget.value })} w={80} />
              </Table.Td>
              <Table.Td>
                <Checkbox checked={a.scored} onChange={(e) => updateRow(i, { scored: e.currentTarget.checked })} />
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
          + Add home attempt
        </Button>
        <Button size="xs" variant="light" onClick={() => addRow('away')}>
          + Add away attempt
        </Button>
      </Group>
    </div>
  )
}
