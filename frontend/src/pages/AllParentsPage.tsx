import { useMemo, useState } from 'react'
import { Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listParents } from '../api/parents'
import { listPlayers } from '../api/players'
import { useAccess } from '../auth/access'

export function AllParentsPage() {
  const [search, setSearch] = useState('')
  const { hideContactDetails } = useAccess()
  const { data: parents, isLoading } = useQuery({ queryKey: ['parents'], queryFn: listParents })
  const { data: players } = useQuery({ queryKey: ['players'], queryFn: () => listPlayers() })

  const childrenByParentId = useMemo(() => {
    const map = new Map<number, string[]>()
    for (const p of players ?? []) {
      if (p.parent_id == null) continue
      const list = map.get(p.parent_id) ?? []
      list.push(p.name)
      map.set(p.parent_id, list)
    }
    return map
  }, [players])

  const filtered = useMemo(() => {
    if (!parents) return []
    const q = search.trim().toLowerCase()
    if (!q) return parents
    return parents.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (childrenByParentId.get(p.id) ?? []).some((c) => c.toLowerCase().includes(q))
    )
  }, [parents, search, childrenByParentId])

  return (
    <Stack>
      <Title order={2}>All Parents</Title>
      <Text size="sm" c="dimmed">
        {parents?.length ?? 0} parent/guardian record(s) on file — created automatically when a player's
        contact info matches or is entered, and linkable/correctable from a player's own profile.
      </Text>
      <TextInput
        placeholder="Search by parent or child name"
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        w={320}
      />
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            {!hideContactDetails && <Table.Th>Phone</Table.Th>}
            {!hideContactDetails && <Table.Th>Email</Table.Th>}
            <Table.Th>Children</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {filtered.map((p) => (
            <Table.Tr key={p.id}>
              <Table.Td>{p.name}</Table.Td>
              {!hideContactDetails && <Table.Td>{p.phone ?? '—'}</Table.Td>}
              {!hideContactDetails && <Table.Td>{p.email ?? '—'}</Table.Td>}
              <Table.Td>{(childrenByParentId.get(p.id) ?? []).join(', ') || '—'}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {!isLoading && filtered.length === 0 && (
        <Text c="dimmed" size="sm">
          No parents found.
        </Text>
      )}
    </Stack>
  )
}
