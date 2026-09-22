export function clampPage(value: unknown, totalPages: number, fallback = 1): number {
  const numeric = Number(value)
  const page = Number.isFinite(numeric) ? Math.trunc(numeric) : fallback
  return Math.min(Math.max(1, Math.trunc(totalPages) || 1), Math.max(1, page))
}

export function parseStudentPaste(value: string): Array<{student_number:string;display_name:string}> {
  return value.split(/\r?\n/).filter(line => line.trim()).map(line => {
    const [student_number, ...name] = line.trim().split(/[\t,，]+/)
    return { student_number, display_name: name.join(' ').trim() }
  })
}
