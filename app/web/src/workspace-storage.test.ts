import { afterEach, describe, expect, it, vi } from 'vitest'
import { readWorkspace, selectAvailableCourse, writeWorkspace } from './workspace-storage'

afterEach(() => vi.unstubAllGlobals())
describe('account workspace memory', () => {
  it('keeps accounts separate and tolerates damaged or disabled storage', () => {
    const data = new Map<string,string>()
    vi.stubGlobal('localStorage', { getItem:(key:string)=>data.get(key), setItem:(key:string,value:string)=>data.set(key,value) })
    writeWorkspace('teacher-a', 'teacher-course', 'course-a')
    expect(readWorkspace('teacher-a', 'teacher-course', '')).toBe('course-a')
    expect(readWorkspace('teacher-b', 'teacher-course', '')).toBe('')
    data.set('zhijiao:workspace:teacher-a:teacher-course', '{broken')
    expect(readWorkspace('teacher-a', 'teacher-course', '')).toBe('')
    vi.stubGlobal('localStorage', { getItem:()=>{ throw Error('disabled') }, setItem:()=>{ throw Error('disabled') } })
    expect(() => writeWorkspace('a', 'course', 'b')).not.toThrow()
    expect(readWorkspace('a', 'course', '')).toBe('')
  })
  it('honors a permitted deep link and discards stale or unauthorized course IDs', () => {
    expect(selectAvailableCourse(['a','b'], 'b', 'a', 'a')).toBe('b')
    expect(selectAvailableCourse(['a','b'], 'private', '', 'b')).toBe('b')
    expect(selectAvailableCourse(['a'], '', '', 'deleted')).toBe('a')
    expect(selectAvailableCourse([], 'a', 'a', 'a')).toBe('')
    expect(selectAvailableCourse(['a'], '', '', '', true)).toBe('')
  })
})
