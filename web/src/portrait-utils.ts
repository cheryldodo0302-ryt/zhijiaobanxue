export function percentage(value: number | null | undefined): string {
  return value == null ? '数据不足' : `${value}%`
}

export function localDateRange(start: string, end: string): { start_at: string; end_at: string } {
  const first = new Date(`${start}T00:00:00`)
  const last = new Date(`${end}T00:00:00`)
  if (!Number.isFinite(first.getTime()) || !Number.isFinite(last.getTime()) || first > last) {
    throw new Error('请选择有效日期范围')
  }
  last.setDate(last.getDate() + 1)
  return { start_at: first.toISOString(), end_at: last.toISOString() }
}

export function taskRank(task: { rank: number | null; expected_count: number; top_percent: number | null; rank_dynamic: boolean }): string {
  if (task.rank == null) return '尚无按时完整提交'
  return `第 ${task.rank}/${task.expected_count}，前 ${task.top_percent}%${task.rank_dynamic ? '（动态）' : ''}`
}

export function dateLabel(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}
