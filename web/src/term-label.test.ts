import { describe, expect, it } from 'vitest'
import { standardTeachingPeriods, termLabel } from './term-label'

describe('termLabel', () => {
  it('只提供第一、第二、第三学期', () => {
    expect(standardTeachingPeriods).toEqual(['第一学期', '第二学期', '第三学期'])
  })

  it('把旧季节名称转换为规范学期名称', () => {
    expect(termLabel({ academic_year: '2028-2029', teaching_period: '秋季学期', term_name: '2028-2029 秋季学期' })).toBe('2028-2029 第一学期')
    expect(termLabel({ academic_year: '2028-2029', teaching_period: '春季学期', term_name: '2028-2029 春季学期' })).toBe('2028-2029 第二学期')
    expect(termLabel({ academic_year: '2028-2029', teaching_period: '夏季学期', term_name: '2028-2029 夏季学期' })).toBe('2028-2029 第三学期')
  })
  it('不再显示全年这种旧学期名称', () => {
    expect(termLabel({ academic_year: '2028-2029', teaching_period: '全年', term_name: '2028-2029 全年' })).toBe('2028-2029')
  })
})
