import { describe, expect, it } from 'vitest'
import { buildMaterialTree } from './material-tree'

describe('student material tree', () => {
  it('groups teacher categories, tags, and files', () => {
    const tree = buildMaterialTree([
      { document_id: 'slides-1', original_name: '第一章.pptx', material_type: 'slides', material_label: '课件', tags: ['第一章', '重点'] },
      { document_id: 'slides-2', original_name: '第二章.pptx', material_type: 'slides', material_label: '课件', tags: ['第二章'] },
      { document_id: 'book-1', original_name: '教材.pdf', material_type: 'textbook', material_label: '教材', tags: [] },
    ])

    expect(tree.map(node => node.label)).toEqual(['课件', '教材'])
    expect(tree[0].count).toBe(2)
    expect(tree[0].children?.map(node => `${node.label}:${node.count}`)).toEqual(expect.arrayContaining(['第一章:1', '第二章:1', '重点:1']))
    const firstChapter = tree[0].children?.find(node => node.label === '第一章')
    expect(firstChapter?.children?.[0]).toMatchObject({
      label: '第一章.pptx',
      document_id: 'slides-1',
      kind: 'document',
    })
    expect(tree[1].children?.[0]).toMatchObject({ label: '教材.pdf', kind: 'document', document_id: 'book-1' })
  })
})
