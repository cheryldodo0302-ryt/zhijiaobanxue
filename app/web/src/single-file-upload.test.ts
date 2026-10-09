import { describe, expect, it } from 'vitest'
import { useSingleFileUpload } from './single-file-upload'

describe('single document selection', () => {
  it('replaces the previous selection and submits the new file', () => {
    const selection = useSingleFileUpload()
    const first = new File(['first'], 'first.txt')
    const second = new File(['second'], 'second.txt')
    selection.replace([first])
    selection.replace([second])
    expect(selection.files.value).toHaveLength(1)
    expect(selection.file.value).toBe(second)
    expect(selection.files.value[0].name).toBe('second.txt')
  })

  it('clears the submitted file when the widget removes it', () => {
    const selection = useSingleFileUpload()
    selection.replace([new File(['text'], 'notes.txt')])
    selection.files.value = []
    expect(selection.file.value).toBeNull()
  })

  it('can choose the same file again after reset', () => {
    const selection = useSingleFileUpload()
    const file = new File(['text'], 'notes.txt')
    selection.replace([file])
    const previousId = selection.files.value[0].uid
    selection.clear()
    expect(selection.file.value).toBeNull()
    expect(selection.files.value).toEqual([])
    selection.replace([file])
    expect(selection.file.value).toBe(file)
    expect(selection.files.value[0].uid).not.toBe(previousId)
  })

  it('keeps a retryable selection when the picker is cancelled', () => {
    const selection = useSingleFileUpload()
    const file = new File(['text'], 'notes.txt')
    selection.replace([file])
    selection.replace([])
    expect(selection.file.value).toBe(file)
  })
})
