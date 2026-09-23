export type StudentMaterial = {
  document_id: string
  original_name?: string
  material_type?: string
  material_label?: string
  tags?: unknown
}

export type MaterialTreeNode = {
  id: string
  label: string
  kind: 'category' | 'tag' | 'document'
  count?: number
  document_id?: string
  children?: MaterialTreeNode[]
}

const MATERIAL_LABELS: Record<string, string> = {
  syllabus: '教学大纲',
  lesson_plan: '教案',
  slides: '课件',
  textbook: '教材',
  experiment: '实验资料',
  question_bank: '题库',
  knowledge_graph: '知识图谱',
  teaching_schedule: '教学进度',
  other: '其他',
}

const MATERIAL_ORDER = Object.keys(MATERIAL_LABELS)
const collator = new Intl.Collator('zh-CN')

function sortByLabel(left: MaterialTreeNode, right: MaterialTreeNode) {
  return collator.compare(left.label, right.label)
}

export function buildMaterialTree(documents: StudentMaterial[]): MaterialTreeNode[] {
  const categories = new Map<string, MaterialTreeNode>()

  for (const document of documents) {
    const categoryKey = String(document.material_type || 'other')
    let category = categories.get(categoryKey)
    if (!category) {
      category = {
        id: `material:${categoryKey}`,
        label: String(document.material_label || MATERIAL_LABELS[categoryKey] || '其他'),
        kind: 'category',
        count: 0,
        children: [],
      }
      categories.set(categoryKey, category)
    }

    const tags = Array.isArray(document.tags)
      ? [...new Set(document.tags.map(tag => String(tag).trim()).filter(Boolean))]
      : []
    category.count = (category.count || 0) + 1
    if (!tags.length) {
      category.children?.push({
        id: `${category.id}:document:${document.document_id}`,
        label: document.original_name || '未命名资料',
        kind: 'document',
        document_id: document.document_id,
      })
      continue
    }

    for (const tag of tags) {
      let tagNode = category.children?.find(child => child.label === tag)
      if (!tagNode) {
        tagNode = {
          id: `${category.id}:tag:${tag}`,
          label: tag,
          kind: 'tag',
          count: 0,
          children: [],
        }
        category.children?.push(tagNode)
      }
      tagNode.count = (tagNode.count || 0) + 1
      tagNode.children?.push({
        id: `${tagNode.id}:document:${document.document_id}`,
        label: document.original_name || '未命名资料',
        kind: 'document',
        document_id: document.document_id,
      })
    }
  }

  return [...categories.entries()]
    .sort(([left], [right]) => {
      const leftIndex = MATERIAL_ORDER.indexOf(left)
      const rightIndex = MATERIAL_ORDER.indexOf(right)
      return (leftIndex < 0 ? MATERIAL_ORDER.length : leftIndex)
        - (rightIndex < 0 ? MATERIAL_ORDER.length : rightIndex)
        || collator.compare(left, right)
    })
    .map(([, category]) => ({
      ...category,
      children: category.children?.sort((left, right) => {
        if (left.kind !== right.kind) return left.kind === 'tag' ? -1 : 1
        return sortByLabel(left, right)
      }),
    }))
}
