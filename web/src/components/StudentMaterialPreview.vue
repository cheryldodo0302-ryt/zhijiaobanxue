<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import KnowledgeMarkdown from './KnowledgeMarkdown.vue'

const props = defineProps<{ courseId: string; documentId?: string; pageNumber?: number | null; sourceName?: string; section?: string; compact?: boolean }>()
const selectedDocumentId = defineModel<string>('selectedDocumentId', { default: '' })
const page = ref(1), totalPages = ref<number | undefined>()
const pageNotice = ref('')
const pdfPageUrl = computed(() => pdfUrl.value ? `${pdfUrl.value}#page=${page.value}&view=FitH` : '')
let slideRenderer: { renderSingleSlide: (index: number) => void; updatePagination: () => void } | undefined
function requestedPage() {
  const value = Number(props.pageNumber)
  return Number.isInteger(value) && value > 0 ? value : 1
}
function goToPage(value: number | undefined) {
  page.value = Math.max(1, Math.min(totalPages.value || Number.MAX_SAFE_INTEGER, Math.floor(Number(value) || 1)))
  if (slideRenderer) { slideRenderer.renderSingleSlide(page.value - 1); slideRenderer.updatePagination() }
}
const files = ref<{ document_id: string; original_name: string }[]>([])
const selectedId = ref(''), kind = ref(''), content = ref(''), pdfUrl = ref('')
const loading = ref(false), listing = ref(false), error = ref(''), listError = ref('')
const pptHost = ref<HTMLElement | null>(null)
const selected = computed(() => files.value.find(file => file.document_id === selectedId.value))
let request = 0, listRequest = 0
let controller: AbortController | undefined

function clearPreview() {
  request++
  controller?.abort()
  slideRenderer = undefined
  totalPages.value = undefined
  pageNotice.value = ''
  if (pdfUrl.value) URL.revokeObjectURL(pdfUrl.value)
  pdfUrl.value = ''; content.value = ''; kind.value = ''; error.value = ''; loading.value = false
  pptHost.value?.replaceChildren()
}

async function loadFiles() {
  const token = ++listRequest
  clearPreview()
  files.value = []; selectedId.value = ''; listError.value = ''; listing.value = true
  try {
    if (props.documentId) {
      files.value = [{ document_id: props.documentId, original_name: props.sourceName || '来源资料' }]
      selectedId.value = props.documentId
      return
    }
    const result = await api.get(`/student/courses/${props.courseId}/documents`)
    if (token !== listRequest) return
    files.value = result.data
    selectedId.value = files.value.some(file => file.document_id === selectedDocumentId.value)
      ? selectedDocumentId.value : files.value[0]?.document_id || ''
  } catch (e: any) {
    if (token === listRequest) listError.value = e.response?.data?.detail || '资料列表加载失败，请刷新重试'
  } finally {
    if (token === listRequest) listing.value = false
  }
}

async function preview() {
  clearPreview()
  if (!selectedId.value) return
  const token = request
  page.value = selectedId.value === props.documentId ? requestedPage() : 1
  controller = new AbortController()
  const signal = controller.signal
  loading.value = true
  try {
    const { data } = await api.post(`/documents/${selectedId.value}/preview-token`, {}, { signal })
    if (token !== request) return
    if (!['pdf', 'pptx', 'docx', 'markdown', 'text'].includes(data.preview_kind))
      throw new Error(data.preview_error || '暂不支持此格式预览，请教师提供 PDF 或 PPTX 文件')
    const response = await fetch(data.preview_kind === 'pptx' ? data.download_url : data.preview_url, { signal, cache: 'no-store' })
    if (!response.ok) throw new Error(response.status === 403
      ? '预览权限已失效或教师已撤回资料，请刷新列表'
      : '文件加载失败，请重新预览')
    const body = data.preview_kind === 'pptx' ? await response.arrayBuffer()
      : data.preview_kind === 'pdf' ? await response.blob() : await response.text()
    if (token !== request) return
    kind.value = data.preview_kind
    if (props.documentId && !props.pageNumber) pageNotice.value = '来源未提供页码，已打开资料首页，请参照章节提示查找。'
    if (props.documentId && ['text', 'markdown', 'docx'].includes(kind.value)) pageNotice.value = '此格式没有可靠的原始分页，请参照章节提示阅读。'
    if (kind.value === 'pdf') pdfUrl.value = URL.createObjectURL(body as Blob)
    else if (kind.value === 'pptx') {
      await nextTick()
      const { init } = await import('pptx-preview')
      if (token !== request || !pptHost.value) return
      // Render into a request-owned element so late renders cannot replace a new file.
      const host = document.createElement('div')
      pptHost.value.replaceChildren(host)
      const width = Math.max(280, Math.min(1100, pptHost.value.clientWidth - 24))
      const renderer = init(host, { width, height: Math.round(width * 9 / 16), mode: props.documentId ? 'slide' : 'list' })
      await renderer.preview(body as ArrayBuffer)
      if (token !== request) return
      if (props.documentId) {
        slideRenderer = renderer
        totalPages.value = renderer.slideCount
        if (page.value > renderer.slideCount) pageNotice.value = '来源页码超出当前幻灯片范围，已定位至最后一页。'
        goToPage(page.value)
      }
    } else content.value = body as string
  } catch (e: any) {
    if (token !== request || signal.aborted) return
    error.value = e.response?.data?.detail || e.message || '预览失败，请重试'
    kind.value = ''
  } finally {
    if (token === request) loading.value = false
  }
}

watch(selectedId, () => {
  if (!listing.value) selectedDocumentId.value = selectedId.value
  preview()
})
watch(selectedDocumentId, value => {
  if (!props.documentId && value !== selectedId.value) selectedId.value = value
})
watch(() => [props.courseId, props.documentId, props.pageNumber], loadFiles, { immediate: true })
onBeforeUnmount(() => { listRequest++; clearPreview() })
</script>

<template>
  <el-card class="student-material-preview" shadow="never">
    <template v-if="!compact" #header><div class="preview-header"><b>{{ props.documentId ? '来源资料预览' : '教师发布资料预览' }}</b><el-button :loading="listing" @click="loadFiles">刷新资料</el-button></div></template>
    <p v-if="!props.documentId" class="muted">选择教师已发布并开放原文件的资料，可直接阅读 PDF、PPTX、Word 和文本。</p>
    <p v-else class="muted">{{ props.sourceName }}<template v-if="props.section"> · {{ props.section }}</template></p>
    <p v-if="pageNotice" class="muted">{{ pageNotice }}</p>
    <el-alert v-if="listError" :title="listError" type="error" :closable="false" />
    <el-empty v-else-if="!listing && !files.length" description="暂无可预览资料，请教师发布知识并开启“学生查看原文件”" />
    <template v-if="files.length">
      <div v-if="!compact" class="preview-toolbar">
        <el-select v-model="selectedId" filterable placeholder="选择课程资料" aria-label="选择预览资料">
          <el-option v-for="file in files" :key="file.document_id" :label="file.original_name" :value="file.document_id" />
        </el-select>
        <el-button :disabled="!selectedId || loading" @click="preview">重新预览</el-button>
      </div>
      <div v-if="!loading && !error && (kind === 'pdf' || (kind === 'pptx' && props.documentId))" class="preview-pagination">
        <span>{{ kind === 'pptx' ? '幻灯片' : 'PDF 页码' }}</span>
        <el-input-number :model-value="page" :min="1" :max="totalPages" :precision="0" aria-label="原文页码" @change="goToPage" />
        <span v-if="totalPages">共 {{ totalPages }} 页</span>
      </div>
      <div v-loading="loading" class="preview-body" element-loading-text="正在加载资料">
        <template v-if="error"><el-alert :title="error" type="error" :closable="false"/><el-button v-if="compact" @click="preview">重新预览</el-button></template>
        <iframe v-else-if="kind === 'pdf' && pdfUrl" :key="pdfPageUrl" :src="pdfPageUrl" :title="selected?.original_name || 'PDF 预览'" />
        <iframe v-else-if="kind === 'docx'" :srcdoc="content" sandbox="" :title="selected?.original_name || 'Word 预览'" />
        <div v-else-if="kind === 'pptx'" ref="pptHost" class="ppt-host" />
        <KnowledgeMarkdown v-else-if="kind === 'markdown'" :content="content" />
        <pre v-else-if="kind === 'text'" class="text-preview">{{ content }}</pre>
      </div>
    </template>
  </el-card>
</template>

<style scoped>
.student-material-preview{margin-bottom:18px;min-width:0}
.preview-pagination{display:flex;align-items:center;gap:10px;margin:12px 0}
.preview-header,.preview-toolbar{display:flex;align-items:center;gap:12px;justify-content:space-between}
.preview-toolbar{margin:16px 0}.preview-toolbar .el-select{min-width:0;flex:1}
.preview-body{min-height:220px;min-width:0}.preview-body iframe{display:block;width:100%;height:70vh;min-height:400px;border:1px solid #dce8e5;border-radius:8px;background:#fff}
.ppt-host{max-height:75vh;overflow:auto;padding:12px;background:#eef3f1}
.text-preview{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8;max-height:70vh;overflow:auto}
@media(max-width:600px){.preview-toolbar{flex-wrap:wrap}.preview-toolbar .el-select{flex-basis:100%}}
</style>
