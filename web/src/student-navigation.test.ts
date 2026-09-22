import { describe, expect, it } from 'vitest'
import { saveStudentDraft, readStudentDraft, clearStudentDrafts } from './student-navigation'

describe('student navigation drafts', () => {
  it('isolates accounts and courses, and returns independent snapshots', () => {
    const draft = { question: '原问题', messages: [{ content: '思考记录' }] }
    saveStudentDraft('u1', 'c1', draft)
    draft.messages[0]!.content = '后续修改'
    expect(readStudentDraft('u1', 'c1').messages[0].content).toBe('思考记录')
    expect(readStudentDraft('u2', 'c1')).toBeNull()
    expect(readStudentDraft('u1', 'c2')).toBeNull()
    const read = readStudentDraft('u1', 'c1')
    read.question = '污染'
    expect(readStudentDraft('u1', 'c1').question).toBe('原问题')
  })
  it('clears only the account that signs out', () => {
    saveStudentDraft('u1', 'c1', { question: '一' })
    saveStudentDraft('u2', 'c1', { question: '二' })
    clearStudentDrafts('u1')
    expect(readStudentDraft('u1', 'c1')).toBeNull()
    expect(readStudentDraft('u2', 'c1').question).toBe('二')
    clearStudentDrafts('u2')
  })
})
