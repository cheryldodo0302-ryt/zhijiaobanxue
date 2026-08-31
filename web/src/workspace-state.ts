export const studentViews = ['qa', 'materials', 'blocks', 'training', 'practice', 'profile', 'graph'] as const
export type StudentView = typeof studentViews[number]

export function normalizeStudentView(value: unknown, sharedCourse = false): StudentView {
  const candidate = String(value || '') as StudentView
  if (!studentViews.includes(candidate)) return 'qa'
  if (candidate === 'graph' && !sharedCourse) return 'qa'
  return candidate
}

export type UploadStage = 'idle' | 'uploading' | 'processing' | 'success' | 'error'

export interface UploadState {
  stage: UploadStage
  progress: number
  message: string
}

export function uploadStageLabel(state: UploadState): string {
  if (state.stage === 'uploading') return `正在上传 ${Math.max(0, Math.min(100, Math.round(state.progress)))}%`
  if (state.stage === 'processing') return '上传完成，正在解析文档'
  if (state.stage === 'success') return '文档已解析并保存'
  if (state.stage === 'error') return state.message || '文档处理失败，可以重试'
  return ''
}
