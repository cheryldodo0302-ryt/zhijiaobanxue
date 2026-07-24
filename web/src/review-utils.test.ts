import { describe, expect, it } from 'vitest'
import { clampPage, parseStudentPaste } from './review-utils'

describe('page navigation', () => {
  it('clamps direct jumps and page boundaries', () => {
    expect(clampPage(0, 12)).toBe(1)
    expect(clampPage(7, 12)).toBe(7)
    expect(clampPage(99, 12)).toBe(12)
    expect(clampPage('bad', 12, 5)).toBe(5)
  })
})

describe('student paste import', () => {
  it('supports number-only, comma and tab rows', () => {
    expect(parseStudentPaste('20260001\n20260002,张三\n20260003\t李四')).toEqual([
      {student_number:'20260001',display_name:''},
      {student_number:'20260002',display_name:'张三'},
      {student_number:'20260003',display_name:'李四'},
    ])
  })
})
