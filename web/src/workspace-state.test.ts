import { describe, expect, it } from 'vitest'
import { normalizeStudentView, uploadStageLabel } from './workspace-state'

describe('student workspace route state', () => {
  it('restores valid views and protects the shared-only graph view', () => {
    expect(normalizeStudentView('materials')).toBe('materials')
    expect(normalizeStudentView('graph', true)).toBe('graph')
    expect(normalizeStudentView('graph', false)).toBe('qa')
    expect(normalizeStudentView('unknown')).toBe('qa')
  })
})

describe('upload status copy', () => {
  it('reports bounded progress and recoverable errors', () => {
    expect(uploadStageLabel({ stage:'uploading', progress:140, message:'' })).toBe('正在上传 100%')
    expect(uploadStageLabel({ stage:'processing', progress:100, message:'' })).toContain('正在解析')
    expect(uploadStageLabel({ stage:'error', progress:0, message:'文件重复' })).toBe('文件重复')
  })
})
