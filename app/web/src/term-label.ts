const PERIOD_LABELS = [
  { label: '第一学期', patterns: ['第一学期', '秋季学期', '秋季', '秋'] },
  { label: '第二学期', patterns: ['第二学期', '春季学期', '春季', '春'] },
  { label: '第三学期', patterns: ['第三学期', '夏季学期', '夏季', '夏'] },
]

function findPeriod(value: unknown) {
  const text = String(value || '').trim()
  return PERIOD_LABELS.find(({ patterns }) => patterns.some(pattern => text.includes(pattern)))?.label || ''
}

function findAcademicYear(value: unknown) {
  const match = String(value || '').match(/20\d{2}(?:\s*[-—至/]\s*20\d{2})?/)
  return match?.[0]?.replace(/\s+/g, '') || ''
}

export function normalizeTeachingPeriod(value: unknown) {
  const text = String(value || '').trim()
  return findPeriod(text) || text
}

export function termLabel(term: Record<string, unknown> | null | undefined) {
  if (!term) return '未设置学期'
  const rawName = String(term.term_name || '').trim()
  const rawPeriod = String(term.teaching_period || '').trim()
  if (rawName === '默认学期' || rawPeriod === '默认学期') return '第一学期'
  const period = findPeriod(rawPeriod) || findPeriod(rawName)
  const year = findAcademicYear(term.academic_year) || findAcademicYear(rawName)
  if (rawName.includes('全年') || rawPeriod.includes('全年')) return year || '未设置学期'
  if (period) return [year, period].filter(Boolean).join(' ')
  return rawName || year || '未设置学期'
}

export function normalizeTermRecord<T extends Record<string, any>>(term: T): T {
  return { ...term, term_name: termLabel(term), academic_year: '', teaching_period: '' }
}

export const standardTeachingPeriods = PERIOD_LABELS.map(({ label }) => label)
