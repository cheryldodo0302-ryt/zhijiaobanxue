// Only navigation and non-sensitive preferences belong here; never credentials,
// learning answers or course content. Storage can be unavailable in private mode.
export function readWorkspace<T>(userId:string, name:string, fallback:T):T {
  if (!userId) return fallback
  try { return JSON.parse(localStorage.getItem(`zhijiao:workspace:${userId}:${name}`) || 'null') ?? fallback }
  catch { return fallback }
}

export function writeWorkspace(userId:string, name:string, value:unknown) {
  if (!userId) return
  try { localStorage.setItem(`zhijiao:workspace:${userId}:${name}`, JSON.stringify(value)) }
  catch { /* Optional browser memory must not prevent using the workspace. */ }
}

export function selectAvailableCourse(ids:string[], requested:unknown, current:unknown, remembered:unknown, allowAll=false):string {
  return [requested, current, remembered].find(value => typeof value === 'string' && ids.includes(value)) as string
    || (allowAll ? '' : ids[0] || '')
}
