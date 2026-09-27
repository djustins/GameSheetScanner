import { Badge } from '@mantine/core'

const COLORS: Record<string, string> = {
  'Moved Up': 'grape',
  'Has Experience': 'blue',
  'Played Before': 'cyan',
  Returning: 'teal',
  New: 'gray',
}

export function ExperienceBadge({ note }: { note: string | null }) {
  if (!note) return null
  return (
    <Badge color={COLORS[note] ?? 'gray'} variant="light">
      {note}
    </Badge>
  )
}
