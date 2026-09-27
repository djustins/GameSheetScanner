export interface Division {
  id: number
  year: number
  season: string
  age_group: string
  category: string | null
}

export interface Team {
  id: number
  name: string
  color: string | null
  deleted_at: string | null
}

export interface Player {
  id: number
  first_name: string
  last_name: string
  nickname: string | null
  name: string
  birth_date: string | null
  current_division_id: number | null
  contact_first_name: string | null
  contact_last_name: string | null
  contact_phone: string | null
  contact_email: string | null
  parent_id: number | null
}

export interface DivisionPlayer extends Player {
  teams: string[]
  grade: string | null
  note: string | null
}

export interface Coach {
  id: number
  first_name: string
  last_name: string
  nickname: string | null
  name: string
}

export interface PlayerHistoryEntry {
  division_id: number
  year: number
  season: string
  age_group: string
  category: string | null
  team_id: number
  team_name: string
  number: string
  position: string | null
  grade: string | null
  coaches: Coach[]
}

export interface StandingsRow {
  team: string
  games_played: number
  wins: number
  losses: number
  ot_wins: number
  ot_losses: number
  ties: number
  points: number
  goals_for: number
  goals_against: number
  goal_diff: number
}

export interface PlayerStatsRow {
  team_id: number | null
  team: string
  number: string
  name: string
  player_id: number | null
  goals: number
  assists: number
  points: number
  penalties: number
  shootout_goals: number
  shootout_misses: number
}
