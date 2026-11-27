import { describe, expect, it } from 'vitest'
import { apiErrorMessage } from './api-error'

describe('API error messages', () => {
  it('renders structured validation errors as readable text', () => {
    expect(apiErrorMessage({ response: { data: { detail: [
      { loc: ['body', 'items'], type: 'too_long', msg: 'List too long' },
    ] } } }, '发布失败')).toBe('单次任务最多选择 1000 道题')
    expect(apiErrorMessage({ response: { data: { detail: [
      { loc: ['body', 'title'], msg: 'Required' },
    ] } } }, '发布失败')).toBe('title：Required')
  })
  it('keeps service messages and falls back on network failures', () => {
    expect(apiErrorMessage({ response: { data: { detail: '教学班没有有效学生，无法发布任务' } } }, '发布失败')).toContain('没有有效学生')
    expect(apiErrorMessage(new Error('Network'), '发布失败')).toBe('发布失败')
  })
})
