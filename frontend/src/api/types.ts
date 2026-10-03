export interface Division {
  id: number
  year: number
  season: string
  age_group: string
  category: string | null
}

export interface DeletedDivision extends Division {
  deleted_at: string
}

export interface Team {
  id: number
  name: string
  color: string | null
  deleted_at: string | null
}

export interface DeletedTeam extends Team {
  division_id: number
  year: number
  season: string
  age_group: string
}

export interface Player {
  id: number
  first_name: string
  last_name: string
  nickname: string | null
  name: string
  birth_date: string | null
  current_division_id: number | null
  // Every division they're registered in this season, main division first.
  division_ids: number[]
  contact_first_name: string | null
  contact_last_name: string | null
  contact_phone: string | null
  contact_email: string | null
  parent_id: number | null
  usa_ball_hockey_id: string | null
  deleted_at?: string | null
}

export interface DivisionPlayer extends Player {
  teams: string[]
  grade: string | null
  grade_is_carryover: boolean
  note: string | null
}

export interface Coach {
  id: number
  first_name: string
  last_name: string | null
  nickname: string | null
  phone: string | null
  email: string | null
  deleted_at: string | null
  name: string
}

export interface CoachTeam {
  team_id: number
  team_name: string
  division_id: number
  year: number
  season: string
  age_group: string
  category: string | null
}

export interface CoachChild {
  id: number
  first_name: string
  last_name: string | null
  nickname: string | null
  current_division_id: number | null
  name: string
}

export interface RosterEntry {
  id: number
  number: string
  name: string
  player_id: number | null
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

export interface DraftPoolPlayer {
  id: number
  first_name: string
  last_name: string | null
  nickname: string | null
  birth_date: string | null
  name: string
  // The position they registered with in this division.
  position: string | null
}

export interface DraftOrderEntry {
  slot: number
  team_id: number
  team_name: string
}

export interface Draft {
  id: number
  division_id: number
  status: 'in_progress' | 'completed'
  current_pick_number: number
  order: DraftOrderEntry[]
  current_team_id?: number | null
  current_team_name?: string | null
  round?: number
}

export interface DraftPick {
  pick_number: number
  round: number
  team_name: string
  first_name: string
  last_name: string | null
  nickname: string | null
  picked_at: string
  player_name: string
}

export interface AutoDraftRun {
  id: number
  division_id: number
  roster_entry_ids: number[]
  coach_assignments: { team_id: number; coach_id: number }[]
  created_at: string
}

export interface AutoDraftResult {
  assigned: number
  teams: number
  warnings: string[]
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

export interface Role {
  id: number
  name: string
  read_only: boolean
  hide_contact_details: boolean
  pages: string[]
}

export interface User {
  id: number
  email: string
  display_name: string | null
  is_admin: boolean
  role_id: number | null
  coach_id: number | null
  deleted_at: string | null
  pages: string[]
  read_only: boolean
  hide_contact_details: boolean
}

export interface Parent {
  id: number
  first_name: string | null
  last_name: string | null
  phone: string | null
  email: string | null
  name: string
}

export interface ScheduleRow {
  id: number
  order_num: number | null
  round: string | null
  game_date: string
  home_team: string
  away_team: string
  start_time: string | null
  end_time: string | null
  location: string | null
  field: string | null
  accounted_for: boolean
  possible_matches: { id: number; game_date: string }[]
  result: {
    game_id: number
    home_team: string
    away_team: string
    home_score: number | null
    away_score: number | null
  } | null
}

export interface Goal {
  side: 'home' | 'away'
  scorer_number: string
  assist1_number: string | null
  assist2_number: string | null
  period: string | null
  time: string | null
}

export interface Penalty {
  side: 'home' | 'away'
  player_number: string
  penalty_type: string
  period: string | null
  time: string | null
}

export interface ShootoutAttempt {
  side: 'home' | 'away'
  round: number
  player_number: string
  scored: boolean
}

export interface GameData {
  game_date: string
  division: string
  home_team: string
  home_color: string | null
  home_final_score: number | null
  away_team: string
  away_color: string | null
  away_final_score: number | null
  goals: Goal[]
  penalties: Penalty[]
  shootout_attempts: ShootoutAttempt[]
  winner?: 'home' | 'away' | 'tie'
}

export interface GameSummary {
  id: number
  game_date: string
  division: string
  home_team: string
  away_team: string
  home_final_score: number | null
  away_final_score: number | null
}

export interface GameListRow extends GameSummary {
  winner: string | null
  ot_winner: string | null
  ot_loser: string | null
  source_file: string
}

export interface ExtractResult {
  label: string
  extracted_data: GameData
  duplicate: GameSummary | null
}

export interface ApiToken {
  id: number
  name: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export interface ApiTokenCreated {
  id: number
  name: string
  token: string
}

export interface Evaluation {
  id: number
  year: number
  season: string
  age_group: string
  team_name: string | null
  grade: string
  created_at: string
}

export interface PlayerMoveNote {
  id: number
  division_id: number
  from_team: string | null
  to_team: string | null
  note: string | null
  created_at: string
}

export interface PlayerRequest {
  id: number
  player_id: number
  name: string
  note: string | null
  hard: boolean
  // "Do not play with": keep the two on different teams.
  avoid: boolean
  sibling: boolean
  direction: 'made' | 'received'
}

export interface PlayerImportCandidate {
  id: number
  name: string
  birth_date: string | null
}

export interface PlayerImportEntry {
  row_number: number
  name: string | null
  first_name: string | null
  last_name: string | null
  birth_date: string | null
  contact_first_name: string | null
  contact_last_name: string | null
  contact_phone: string | null
  contact_email: string | null
  team_name: string | null
  number: string | null
  position: string | null
  coach_name: string | null
  request_text: string | null
  sibling_text: string | null
  status: 'invalid' | 'create' | 'update' | 'ambiguous' | 'conflict'
  matched_player_id: number | null
  candidates: PlayerImportCandidate[]
  conflict_detail: { existing: string | null; incoming: string | null; sibling_match: string | null } | null
  resolved_action: 'invalid' | 'create' | 'update' | 'use_existing' | null
  resolved_player_id: number | null
}

export interface PlayerImportPlanResponse {
  columns: Record<string, string>
  plan: PlayerImportEntry[]
}

export interface PlayerImportResult {
  created: number
  updated: number
  skipped: number
  rostered: number
  coached: number
  requested: number
  siblings: number
  warnings: string[]
}

// One play-with / do-not-play-with pair in a division (GET /divisions/{id}/requests).
export interface DivisionRequest {
  id: number
  player_id: number
  name: string
  team: string | null
  other_player_id: number
  other_name: string
  other_team: string | null
  other_in_division: boolean
  hard: boolean
  avoid: boolean
  note: string | null
}

// [years, months, days]
export type AgeYMD = [number, number, number]

export interface TeamOverview {
  team_id: number
  name: string
  color: string | null
  coaches: string[]
  players: number
  graded: number
  // This age group's own grades, e.g. { A: 2, B: 1 }.
  grades: Record<string, number>
  // Grades from a different age group (shown as A*), weighted as a D in `average`.
  move_up_grades: Record<string, number>
  average: number | null
  goalies: number
  avg_age: AgeYMD | null
}

export interface AutoDraftRow {
  team_id: number
  team: string
  coach: string | null
  parent_coach: string | null
  number: string
  player_id: number
  name: string
  grade: string | null
  draft_grade: string
  birth_date: string | null
  position: string | null
  goalie: boolean
  requests: { name: string; hard: boolean; avoid: boolean; together: boolean }[]
}

export interface AutoDraftResults {
  run: { created_at: string; warnings: string[] } | null
  rows: AutoDraftRow[]
}

export interface TeamBalance {
  name: string
  player_ids: number[]
  skill: number
  goalies: number
  players: number
  avg_skill: number | null
  avg_age: AgeYMD | null
}

export interface TradeEffect {
  name: string
  other_name: string
  kind: 'sibling' | 'hard' | 'soft' | 'avoid'
  joined: boolean
}

export interface TradePreview {
  now: Record<string, TeamBalance>
  after: Record<string, TeamBalance>
  effects: TradeEffect[]
}

export interface TradeBody {
  team_a: number
  from_a: number[]
  team_b: number
  from_b: number[]
  note?: string | null
  allow_split?: boolean
}

export interface PlayerRegistration {
  division_id: number
  position: string | null
  main: boolean
}

export interface DivisionEvaluation {
  id: number
  player_id: number
  name: string
  grade: string
  created_at: string | null
  team_name: string | null
  number: string | null
}

export interface DivisionEvaluations {
  evaluations: DivisionEvaluation[]
  not_evaluated: { player_id: number; name: string; teams: string[] }[]
}
