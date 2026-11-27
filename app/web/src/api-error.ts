export function apiErrorMessage(error: any, fallback: string): string {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map(issue => {
      const field = issue.loc?.filter((part: unknown) => part !== 'body').join('.')
      if (field === 'items' && issue.type === 'too_long') return '单次任务最多选择 1000 道题'
      return typeof issue.msg === 'string' ? `${field ? `${field}：` : ''}${issue.msg}` : ''
    }).filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  return typeof detail?.message === 'string' ? detail.message : fallback
}
