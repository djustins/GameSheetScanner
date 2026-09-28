export const GRADE_TIERS = ['A', 'B', 'C', 'D']
export const GRADE_VALUES: Record<string, number> = Object.fromEntries(
  GRADE_TIERS.map((tier, i) => [tier, GRADE_TIERS.length - i])
)

export function gradeBreakdown(grades: (string | null | undefined)[]): {
  gradedCount: number
  average: number | null
  breakdown: string
} {
  const tiered = grades
    .map((g) => (g ?? '').trim().toUpperCase())
    .filter((g) => g in GRADE_VALUES)

  if (tiered.length === 0) {
    return { gradedCount: 0, average: null, breakdown: '—' }
  }
  const average = tiered.reduce((sum, g) => sum + GRADE_VALUES[g], 0) / tiered.length
  const breakdown = GRADE_TIERS.filter((tier) => tiered.includes(tier))
    .map((tier) => `${tier}: ${tiered.filter((g) => g === tier).length}`)
    .join(', ')
  return { gradedCount: tiered.length, average, breakdown }
}
