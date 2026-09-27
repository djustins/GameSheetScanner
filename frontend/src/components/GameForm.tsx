import { useState } from 'react'
import { Alert, Autocomplete, Button, Group, NumberInput, Select, Stack, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { listDivisionTeams } from '../api/divisions'
import { getAgeGroups } from '../api/meta'
import type { GameData } from '../api/types'
import { GoalsEditor } from './GoalsEditor'
import { PenaltiesEditor } from './PenaltiesEditor'
import { ShootoutEditor } from './ShootoutEditor'

function emptyGame(): GameData {
  return {
    game_date: '',
    division: '',
    home_team: '',
    home_color: '',
    home_final_score: 0,
    away_team: '',
    away_color: '',
    away_final_score: 0,
    goals: [],
    penalties: [],
    shootout_attempts: [],
  }
}

export function GameForm({
  initialData,
  divisionId,
  onSubmit,
  submitLabel,
  submitting,
  extraActions,
}: {
  initialData?: Partial<GameData>
  divisionId: number
  onSubmit: (data: GameData) => void
  submitLabel: string
  submitting: boolean
  extraActions?: React.ReactNode
}) {
  const [form, setForm] = useState<GameData>({ ...emptyGame(), ...initialData })

  const { data: teams } = useQuery({
    queryKey: ['division-teams', divisionId],
    queryFn: () => listDivisionTeams(divisionId),
  })
  const { data: ageGroups } = useQuery({ queryKey: ['age-groups'], queryFn: getAgeGroups })
  const teamNames = (teams ?? []).map((t) => t.name)

  function set<K extends keyof GameData>(key: K, value: GameData[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function recalcScore() {
    const home = form.goals.filter((g) => g.side === 'home').length
    const away = form.goals.filter((g) => g.side === 'away').length
    setForm((f) => ({ ...f, home_final_score: home, away_final_score: away }))
  }

  function swapSides() {
    setForm((f) => ({
      ...f,
      home_team: f.away_team,
      away_team: f.home_team,
      home_color: f.away_color,
      away_color: f.home_color,
      home_final_score: f.away_final_score,
      away_final_score: f.home_final_score,
      goals: f.goals.map((g) => ({ ...g, side: g.side === 'home' ? 'away' : 'home' })),
      penalties: f.penalties.map((p) => ({ ...p, side: p.side === 'home' ? 'away' : 'home' })),
      shootout_attempts: f.shootout_attempts.map((s) => ({ ...s, side: s.side === 'home' ? 'away' : 'home' })),
      winner: f.winner === 'home' ? 'away' : f.winner === 'away' ? 'home' : f.winner,
    }))
  }

  const shootoutMismatch =
    form.shootout_attempts.length > 0 && form.home_final_score !== form.away_final_score && form.winner !== 'tie'

  return (
    <Stack>
      <Group grow>
        <TextInput label="Game date" value={form.game_date} onChange={(e) => set('game_date', e.currentTarget.value)} placeholder="7/14/26" />
        <Select
          label="Division"
          data={Object.keys(ageGroups ?? {})}
          value={form.division || null}
          onChange={(v) => set('division', v ?? '')}
          searchable
        />
      </Group>

      <Button size="xs" variant="subtle" onClick={swapSides} w={200}>
        Swap Home/Away
      </Button>

      <Title order={5}>Home</Title>
      <Group grow>
        <Autocomplete
          label="Home team"
          data={teamNames}
          value={form.home_team}
          onChange={(v) => set('home_team', v)}
        />
        <TextInput label="Home color" value={form.home_color ?? ''} onChange={(e) => set('home_color', e.currentTarget.value)} />
        <NumberInput label="Home final score" value={form.home_final_score ?? 0} onChange={(v) => set('home_final_score', Number(v) || 0)} min={0} />
      </Group>

      <Title order={5}>Away</Title>
      <Group grow>
        <Autocomplete
          label="Away team"
          data={teamNames}
          value={form.away_team}
          onChange={(v) => set('away_team', v)}
        />
        <TextInput label="Away color" value={form.away_color ?? ''} onChange={(e) => set('away_color', e.currentTarget.value)} />
        <NumberInput label="Away final score" value={form.away_final_score ?? 0} onChange={(v) => set('away_final_score', Number(v) || 0)} min={0} />
      </Group>
      <Button size="xs" variant="subtle" w={220} onClick={recalcScore}>
        Recalculate score from goals
      </Button>

      <Title order={5} mt="sm">
        Goals
      </Title>
      <GoalsEditor goals={form.goals} onChange={(goals) => set('goals', goals)} />

      <Title order={5} mt="sm">
        Penalties
      </Title>
      <PenaltiesEditor penalties={form.penalties} onChange={(penalties) => set('penalties', penalties)} />

      <Title order={5} mt="sm">
        Shootout
      </Title>
      <ShootoutEditor attempts={form.shootout_attempts} onChange={(shootout_attempts) => set('shootout_attempts', shootout_attempts)} />

      <Select
        label="Winner"
        data={[
          { value: 'home', label: form.home_team || 'Home' },
          { value: 'away', label: form.away_team || 'Away' },
          { value: 'tie', label: 'Tie' },
        ]}
        value={form.winner ?? null}
        onChange={(v) => set('winner', (v as GameData['winner']) ?? undefined)}
      />

      {shootoutMismatch && (
        <Alert color="yellow">
          Shootout attempts are recorded, but the score isn&apos;t tied — a shootout only happens after a tied
          game. This won&apos;t save until the score is tied or the shootout attempts are removed.
        </Alert>
      )}

      <Group>
        <Button loading={submitting} onClick={() => onSubmit(form)}>
          {submitLabel}
        </Button>
        {extraActions}
      </Group>
    </Stack>
  )
}
