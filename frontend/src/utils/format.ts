export function divisionLabel(ageGroup: string, category: string | null): string {
  return category ? `${ageGroup} (${category})` : ageGroup
}

export function divisionSeasonLabel(division: { year: number; season: string; age_group: string; category: string | null }): string {
  return `${division.year} ${division.season} — ${divisionLabel(division.age_group, division.category)}`
}

export function coachLabel(coach: { name: string; nickname: string | null }): string {
  return coach.nickname ? `${coach.name} "${coach.nickname}"` : coach.name
}
