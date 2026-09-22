<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { api } from '../api'
import { clampPage } from '../review-utils'

const courses = ref<any[]>([])
const jobs = ref<any[]>([])
const blocks = ref<any[]>([])
const questions = ref<any[]>([])
const health = ref<any>(null)
const courseId = ref('')
const selectedDocument = ref<any>(null)
const selectedPage = ref(1)
const pageInput = ref(1)
const activeReviewTab = ref('knowledge')
const previewUrl = ref('')
const file = ref<File | null>(null)
const maxUploadMb = ref(500)
const uploadPercent = ref(0)
const uploading = ref(false)
let pollTimer: number | undefined

const fail = (error: any, fallback: string) => ElMessage.error(error.response?.data?.detail || fallback)
const totalPages = computed(() => Math.max(1, Number(selectedDocument.value?.total_pages || 0), ...blocks.value.map(block => Number(block.page_number || 1))))
const pageBlocks = computed(() => blocks.value.filter(block => block.content_destination === 'knowledge' && ['title','paragraph','formula','code','list'].includes(block.block_type) && Number(block.page_number || 1) === selectedPage.value))
const routedBlocks = computed(() => blocks.value.filter(block => ['unclassified','excluded'].includes(block.content_destination)))
const isPdf = computed(() => selectedDocument.value?.mime_type === 'application/pdf')
const pdfPreviewUrl = computed(() => previewUrl.value ? `${previewUrl.value}#page=${selectedPage.value}&view=FitH` : '')

async function load() {
  const [courseResult, capabilityResult] = await Promise.all([
    api.get('/teacher/courses'), api.get('/system/capabilities'),
  ])
  courses.value = courseResult.data
  maxUploadMb.value = capabilityResult.data.max_upload_mb
  if (!courseId.value && courses.value.length) courseId.value = courses.value[0].course_id
  await loadJobs()
}

async function loadJobs() {
  if (!courseId.value) return
  const [jobResult, healthResult] = await Promise.all([
    api.get(`/teacher/courses/${courseId.value}/ingestion-jobs`),
    api.get(`/teacher/courses/${courseId.value}/knowledge-health`),
  ])
  jobs.value = jobResult.data
  health.value = healthResult.data
  questions.value = (await api.get(`/teacher/courses/${courseId.value}/question-bank`)).data
}

function choose(upload: UploadFile) {
  const selected = upload.raw || null
  if (selected && selected.size > maxUploadMb.value * 1024 * 1024) {
    file.value = null
    ElMessage.error(`文件不能超过 ${maxUploadMb.value}MB`)
    return
  }
  file.value = selected
}

async function upload() {
  if (!file.value || !courseId.value) return
  const body = new FormData()
  body.append('file', file.value)
  uploading.value = true
  uploadPercent.value = 0
  try {
    await api.post(`/teacher/courses/${courseId.value}/documents`, body, {
      timeout: 0,
      onUploadProgress: event => {
        if (event.total) uploadPercent.value = Math.round(event.loaded * 100 / event.total)
      },
    })
    file.value = null
    ElMessage.success('原文件已保存，知识解析任务已进入队列')
    await loadJobs()
  } catch (error) { fail(error, '上传失败') }
  finally { uploading.value = false }
}

async function openReview(job: any) {
  try {
    const [blockResult, previewResult] = await Promise.all([
      api.get(`/teacher/documents/${job.document_id}/blocks`, { params: { limit: 100 } }),
      api.post(`/documents/${job.document_id}/preview-token`),
    ])
    blocks.value = blockResult.data
    selectedDocument.value = { ...job, ...previewResult.data }
    previewUrl.value = previewResult.data.preview_url
    const pending = blocks.value.find(block => block.verification_status === 'review_required')
    selectedPage.value = Number(pending?.page_number || blocks.value[0]?.page_number || 1)
    pageInput.value = selectedPage.value
  } catch (error) { fail(error, '审核资料加载失败') }
}

async function goPage(value = pageInput.value) {
  selectedPage.value = clampPage(value, totalPages.value, selectedPage.value)
  pageInput.value = selectedPage.value
  if (!selectedDocument.value) return
  try {
    blocks.value = (await api.get(`/teacher/documents/${selectedDocument.value.document_id}/blocks`, {
      params: { page_number: selectedPage.value, limit: 200 },
    })).data
  } catch (error) { fail(error, '当前页知识块加载失败') }
}

async function routeBlock(block: any, destination: string) {
  try {
    const { data } = await api.patch(`/teacher/blocks/${block.block_id}/classification`, {
      destination, semantic_role: block.semantic_role || (destination === 'knowledge' ? 'explanation' : ''),
      question_group_key: block.question_group_key || '', reason: '教师手动调整',
    })
    Object.assign(block, data)
    questions.value = (await api.get(`/teacher/courses/${courseId.value}/question-bank`)).data
  } catch (error) { fail(error, '内容重新分类失败') }
}

async function reviewQuestion(item: any, status: 'approved'|'rejected') {
  try { Object.assign(item, (await api.patch(`/teacher/question-bank/${item.item_id}`, { ...item, status })).data); ElMessage.success(status === 'approved' ? '习题已批准' : '习题已驳回') }
  catch (error) { fail(error, '习题审核失败') }
}

async function publishQuestions() {
  try { const {data}=await api.post(`/teacher/courses/${courseId.value}/question-bank/publish`);ElMessage.success(`习题库 v${data.version_number} 已发布`) }
  catch(error){fail(error,'习题库发布失败')}
}

async function review(block: any, accepted: boolean) {
  try {
    const { data } = await api.patch(`/teacher/blocks/${block.block_id}/review`, {
      markdown: block.markdown,
      plain_text: block.markdown,
      latex: block.latex,
      visibility_level: block.visibility_level,
      accepted,
    })
    block.verification_status = data.verification_status
    ElMessage.success(accepted ? '该知识块已确认' : '该知识块已驳回')
    await loadJobs()
  } catch (error) { fail(error, '审核失败') }
}

async function setStudentVisible(value: boolean) {
  if (!selectedDocument.value) return
  try {
    await api.patch(`/teacher/documents/${selectedDocument.value.document_id}/student-visibility`, { visible: value })
    selectedDocument.value.student_file_visible = value ? 1 : 0
    const job = jobs.value.find(item => item.document_id === selectedDocument.value.document_id)
    if (job) job.student_file_visible = value ? 1 : 0
    ElMessage.success(value ? '发布知识库后，学生可查看原文件' : '已关闭学生原文件查看权限')
  } catch (error) { fail(error, '原文件可见性设置失败') }
}

async function publish() {
  try {
    const { data } = await api.post(`/teacher/courses/${courseId.value}/knowledge-versions/publish`)
    ElMessage.success(`知识库 Markdown 版本 v${data.version_number} 已发布`)
    await loadJobs()
  } catch (error) { fail(error, '发布失败') }
}

async function control(job: any, action: 'cancel' | 'retry') {
  try { await api.post(`/teacher/ingestion-jobs/${job.job_id}/${action}`); await loadJobs() }
  catch (error) { fail(error, action === 'cancel' ? '取消失败' : '重试失败') }
}

onMounted(async () => {
  await load().catch(error => fail(error, '页面加载失败'))
  pollTimer = window.setInterval(() => loadJobs().catch(() => undefined), 4000)
})
onUnmounted(() => { if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <main class="content knowledge-workbench">
    <el-page-header content="可信资料入库" @back="$router.push('/')" />
    <div class="page-title">
      <span class="eyebrow">KNOWLEDGE INGESTION</span>
      <h1>课程知识库</h1>
      <p class="muted">原文件用于师生查看；审核后的 Markdown / DocumentIR 用于检索、问答与练习。</p>
    </div>

    <el-row v-if="health" :gutter="12" class="health-row">
      <el-col v-for="item in [['总页数',health.total_pages],['原生页',health.native_pages],['OCR 页',health.ocr_pages],['公式',health.formula_count],['表格',health.table_count],['待审核',health.pending_regions],['失败页',health.failed_pages],['云端 Token',health.cloud_tokens]]" :key="item[0]" :span="3">
        <el-card shadow="never"><small>{{ item[0] }}</small><strong>{{ item[1] }}</strong></el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="toolbar-card">
      <el-row :gutter="16" align="middle">
        <el-col :span="7"><el-select v-model="courseId" placeholder="选择课程" @change="loadJobs"><el-option v-for="course in courses" :key="course.course_id" :label="course.course_name" :value="course.course_id" /></el-select></el-col>
        <el-col :span="10"><el-upload :auto-upload="false" :limit="1" :on-change="choose"><el-button>选择 PDF / PPTX / DOCX</el-button><template #tip><div class="el-upload__tip">单文件上限 {{ maxUploadMb }}MB，大文件采用流式落盘</div></template></el-upload></el-col>
        <el-col :span="7"><el-button type="primary" :loading="uploading" :disabled="!file" @click="upload">开始入库</el-button><el-button @click="loadJobs">刷新</el-button><el-progress v-if="uploading" :percentage="uploadPercent" /></el-col>
      </el-row>
    </el-card>

    <el-card shadow="never">
      <template #header><div class="card-header"><b>资料与解析任务</b><el-button type="success" @click="publish">发布知识库 Markdown</el-button></div></template>
      <el-table :data="jobs" empty-text="暂无资料">
        <el-table-column prop="original_name" label="原文件" min-width="220" />
        <el-table-column prop="status" label="解析状态" width="140" />
        <el-table-column label="大小" width="100"><template #default="scope">{{ (scope.row.size_bytes / 1024 / 1024).toFixed(1) }}MB</template></el-table-column>
        <el-table-column prop="total_pages" label="页数" width="80" />
        <el-table-column label="学生原文件" width="120"><template #default="scope"><el-tag :type="scope.row.student_file_visible ? 'success' : 'info'">{{ scope.row.student_file_visible ? '允许查看' : '未开放' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" min-width="210"><template #default="scope">
          <el-button link type="primary" :disabled="!['ready','review_required'].includes(scope.row.status)" @click="openReview(scope.row)">对照审核</el-button>
          <el-button v-if="['queued','running'].includes(scope.row.status)" link type="danger" @click="control(scope.row,'cancel')">取消</el-button>
          <el-button v-if="['failed','cancelled'].includes(scope.row.status)" link type="warning" @click="control(scope.row,'retry')">重试</el-button>
        </template></el-table-column>
      </el-table>
    </el-card>

    <section v-if="selectedDocument" class="review-shell">
      <div class="review-heading">
        <div><span class="eyebrow">SIDE-BY-SIDE REVIEW</span><h2>{{ selectedDocument.original_name }}</h2></div>
        <div class="source-visibility"><span>学生查看原文件</span><el-switch :model-value="Boolean(selectedDocument.student_file_visible)" @change="setStudentVisible(Boolean($event))" /></div>
      </div>
      <el-alert title="左侧内容进入知识库；右侧原文件仅用于展示。关闭原文件权限不会删除已审核的知识块。" type="info" :closable="false" />
      <div class="page-selector"><el-button :disabled="selectedPage<=1" @click="goPage(selectedPage-1)">上一页</el-button><el-input-number v-model="pageInput" :min="1" :max="totalPages" :controls="false" @keyup.enter="goPage()"/><el-button @click="goPage()">跳转</el-button><strong>{{selectedPage}} / {{totalPages}}</strong><el-button :disabled="selectedPage>=totalPages" @click="goPage(selectedPage+1)">下一页</el-button></div>

      <el-tabs v-model="activeReviewTab" class="review-tabs">
      <el-tab-pane label="知识库审核" name="knowledge">
      <div class="review-columns">
        <el-card shadow="never" class="markdown-pane">
          <template #header><div class="pane-title"><b>知识库 Markdown</b><span>第 {{ selectedPage }} 页 · {{ pageBlocks.length }} 个块</span></div></template>
          <div v-if="!pageBlocks.length" class="empty-pane">本页没有知识点；内容可能已进入习题库或被排除。</div>
          <div v-for="block in pageBlocks" :key="block.block_id" class="block-review">
            <div class="block-meta"><el-tag>{{ block.block_type }}</el-tag><el-tag :type="block.verification_status === 'teacher_verified' || block.verification_status === 'auto_verified' ? 'success' : 'warning'">{{ block.verification_status }}</el-tag><span v-if="block.confidence !== null">置信度 {{ Number(block.confidence).toFixed(2) }}</span></div>
            <el-input v-model="block.markdown" type="textarea" :autosize="{ minRows: 5, maxRows: 16 }" placeholder="编辑进入知识库的 Markdown" />
            <el-input v-if="block.block_type === 'formula'" v-model="block.latex" class="latex-input" placeholder="LaTeX" />
            <div class="block-actions"><el-select v-model="block.visibility_level"><el-option v-for="level in ['PUBLIC','GUIDANCE','ASSESSMENT','VAULT']" :key="level" :label="level" :value="level" /></el-select><el-button type="success" @click="review(block,true)">确认</el-button><el-button type="danger" plain @click="review(block,false)">驳回</el-button></div>
          </div>
        </el-card>

        <el-card shadow="never" class="source-pane">
          <template #header><div class="pane-title"><b>原文件预览</b><span>第 {{ selectedPage }} 页</span></div></template>
          <iframe v-if="isPdf && pdfPreviewUrl" :key="pdfPreviewUrl" :src="pdfPreviewUrl" title="原始 PDF 对照预览" />
          <div v-else class="office-preview"><p>浏览器不能可靠地逐页预览此 Office 文件，请打开原文件对照。</p><el-button type="primary" tag="a" :href="previewUrl" target="_blank">打开/下载 {{ selectedDocument.original_name }}</el-button></div>
        </el-card>
      </div>
      </el-tab-pane>
      <el-tab-pane label="习题库草稿" name="questions"><div class="card-header"><p class="muted">例题、习题、答案、解析与依赖图表在此独立审核。</p><el-button type="success" @click="publishQuestions">发布已批准习题</el-button></div><el-empty v-if="!questions.length" description="暂无习题草稿"/><el-card v-for="item in questions" :key="item.item_id" shadow="never" class="question-card"><div class="block-meta"><el-select v-model="item.question_type"><el-option label="单选题" value="single_choice"/><el-option label="判断题" value="true_false"/><el-option label="简答题" value="short_answer"/><el-option label="其他" value="other"/></el-select><el-tag>{{item.status}}</el-tag><span>来源页 {{item.source_pages.join(', ')}}</span></div><el-input v-model="item.stem_markdown" type="textarea" :autosize="{minRows:3,maxRows:10}" placeholder="题干"/><el-input v-model="item.answer_markdown" type="textarea" :autosize="{minRows:2,maxRows:6}" placeholder="答案"/><el-input v-model="item.explanation_markdown" type="textarea" :autosize="{minRows:2,maxRows:8}" placeholder="解析"/><p v-if="item.attachments.length">附件 {{item.attachments.length}} 个（保留原页与坐标）</p><div class="block-actions"><el-button type="success" @click="reviewQuestion(item,'approved')">批准</el-button><el-button type="danger" plain @click="reviewQuestion(item,'rejected')">驳回</el-button></div></el-card></el-tab-pane>
      <el-tab-pane label="待分类 / 已排除" name="routing"><el-empty v-if="!routedBlocks.length" description="没有待处理内容"/><div v-for="block in routedBlocks" :key="block.block_id" class="block-review"><div class="block-meta"><el-tag :type="block.content_destination==='unclassified'?'warning':'info'">{{block.content_destination}}</el-tag><el-tag>{{block.block_type}}</el-tag><span>第 {{block.page_number}} 页</span></div><p>{{block.markdown||block.plain_text||block.latex||'图片/表格原始区域'}}</p><p class="muted">{{block.analysis_reason}}</p><div class="block-actions"><el-button :disabled="['image','table'].includes(block.block_type)" @click="routeBlock(block,'knowledge')">转入知识库</el-button><el-button @click="routeBlock(block,'question_bank')">转入习题库</el-button><el-button @click="routeBlock(block,'excluded')">排除</el-button></div></div></el-tab-pane>
      </el-tabs>
    </section>
  </main>
</template>
