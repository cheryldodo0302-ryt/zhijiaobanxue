// In-memory drafts survive route unmounts, never shared across accounts or courses.
const drafts = new Map<string, any>()
const key = (user: string, course: string) => JSON.stringify([user, course])
export function saveStudentDraft(user: string, course: string, draft: any) {
  if (!user || !course) return
  const id = key(user, course)
  drafts.delete(id)
  drafts.set(id, JSON.parse(JSON.stringify(draft)))
  if (drafts.size > 30) drafts.delete(drafts.keys().next().value!)
}
export function readStudentDraft(user: string, course: string) {
  const value = drafts.get(key(user, course))
  return value ? JSON.parse(JSON.stringify(value)) : null
}
export function clearStudentDrafts(user: string) {
  for (const id of drafts.keys()) if (JSON.parse(id)[0] === user) drafts.delete(id)
}
