export function divisionLabel(ageGroup: string, category: string | null): string {
  return category ? `${ageGroup} (${category})` : ageGroup
}

export function divisionSeasonLabel(division: { year: number; season: string; age_group: string; category: string | null }): string {
  return `${division.year} ${division.season} — ${divisionLabel(division.age_group, division.category)}`
}

export function coachLabel(coach: { name: string; nickname: string | null }): string {
  return coach.nickname ? `${coach.name} "${coach.nickname}"` : coach.name
}

// An average age from the API ([years, months, days]) as shown, e.g. "9y 4m 12d".
export function formatAge(age: [number, number, number] | null | undefined): string {
  return age ? `${age[0]}y ${age[1]}m ${age[2]}d` : '—'
}
