<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const courses = ref<any[]>([])
const classes = ref<any[]>([])
const courseId = ref('')
const classId = ref('')
const items = ref<any[]>([])
const folders = ref<any[]>([])
const folderFilter = ref('all')
const importFolderId = ref('')
const selectedItems = ref<any[]>([])
const moveTargetFolder = ref('')
const draggingIds = ref<string[]>([])
const dragOverFolder = ref('')
const newFolderName = ref('')
const newFolderType = ref('homework')
const imports = ref<any[]>([])
const statistics = ref<any>(null)
const activeTab = ref('manage')
const statusFilter = ref('all')
const loading = ref(false)
const uploading = ref(false)
const uploadFile = ref<File | null>(null)
const folderFiles = ref<File[]>([])
const aiMode = ref('auto')
const useOwnApi = ref(false)
const aiProvider = ref('openai_compatible')
const aiBaseUrl = ref('')
const aiModel = ref('')
const aiApiKey = ref('')
const fail = (error: any, fallback: string) =>
  ElMessage.error(error.response?.data?.detail || fallback)
const hasIssue = (item: any) => item.status === 'draft' && (
  !item.answer_markdown || Number(item.recognition_confidence || 0) < 0.7
  || (item.recognition_notes || []).length > 0
)
const judgeChoices = (item: any) => {
  const values = (item.options || []).map((option: any) => String(option.text || option.key || '').trim()).filter(Boolean)
  return values.length >= 2 ? [...new Set(values)] : ['T', 'F']
}
const filteredItems = computed(() => items.value
  .filter(item => statusFilter.value === 'all' || item.status === statusFilter.value)
  .filter(item => folderFilter.value === 'all'
    || (folderFilter.value === 'unfiled' ? !item.folder_id : item.folder_id === folderFilter.value))
  .sort((a, b) => Number(hasIssue(b)) - Number(hasIssue(a))))
const summary = computed(() => ({
  total: items.value.length,
  draft: items.value.filter(item => item.status === 'draft').length,
  approved: items.value.filter(item => item.status === 'approved').length,
  rejected: items.value.filter(item => item.status === 'rejected').length,
}))
const folderGroups = computed(() => ([
  { type: 'exam', title: '试卷', subtitle: '阶段测验与正式考试', folders: folders.value.filter(folder => folder.folder_type === 'exam') },
  { type: 'homework', title: '作业', subtitle: '课后作业与提交任务', folders: folders.value.filter(folder => folder.folder_type === 'homework') },
  { type: 'chapter', title: '章节练习', subtitle: '按章节组织的练习集', folders: folders.value.filter(folder => !['exam','homework'].includes(folder.folder_type)) },
]))
const allVisibleSelected = computed(() => filteredItems.value.length > 0 && filteredItems.value.every(item => selectedItems.value.some(value => value.item_id === item.item_id)))

async function loadBase() {
  loading.value = true
  try {
    courses.value = (await api.get('/teacher/courses')).data
    if (!courseId.value && courses.value.length) courseId.value = courses.value[0].course_id
    await changeCourse()
  } catch (error) {
    fail(error, '课程列表加载失败')
  } finally {
    loading.value = false
  }
}
async function changeCourse(switched = false) {
  if (switched) {
    selectedItems.value = []
    folderFilter.value = 'all'
    importFolderId.value = ''
    moveTargetFolder.value = ''
  }
  if (!courseId.value) {
    items.value = []
    imports.value = []
    classes.value = []
    folders.value = []
    statistics.value = null
    return
  }
  loading.value = true
  classId.value = ''
  statistics.value = null
  try {
    const [questions, importHistory, classList, folderList] = await Promise.all([
      api.get(`/teacher/courses/${courseId.value}/question-bank`),
      api.get(`/teacher/courses/${courseId.value}/question-bank/imports`),
      api.get('/teacher/classes', { params: { course_id: courseId.value } }),
      api.get(`/teacher/courses/${courseId.value}/question-folders`),
    ])
    items.value = questions.data
    imports.value = importHistory.data
    classes.value = classList.data
    folders.value = folderList.data
  } catch (error) { fail(error, '题库加载失败') } finally { loading.value = false }
}
function chooseUpload(file: any) { uploadFile.value = file.raw }
function chooseFolderUpload(event: Event) {
  const input = event.target as HTMLInputElement
  folderFiles.value = Array.from(input.files || []).filter(file => /\.xlsx?$/i.test(file.name))
  if (!folderFiles.value.length) ElMessage.warning('所选目录中没有 XLS 或 XLSX 题库')
}
async function ensureFolderPath(path: string) {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean).slice(0, -1)
  let parentId: string | null = null
  let currentPath = ''
  for (const part of parts) {
    currentPath = currentPath ? `${currentPath}/${part}` : part
    let folder = folders.value.find(item => item.relative_path === currentPath)
    if (!folder) {
      folder = (await api.post(`/teacher/courses/${courseId.value}/question-folders`, {
        folder_name: part, folder_type: 'chapter', parent_folder_id: parentId, relative_path: currentPath,
      })).data
      folders.value.push(folder)
    }
    parentId = folder.folder_id
  }
  return parentId
}
async function importFolderPackage() {
  if (!courseId.value || !folderFiles.value.length) return
  uploading.value = true
  let accepted = 0
  let failed = 0
  try {
    for (const file of folderFiles.value) {
      try {
        const relativePath = (file as any).webkitRelativePath || file.name
        const targetFolder = await ensureFolderPath(relativePath)
        const form = new FormData()
        form.append('file', file)
        form.append('ai_mode', 'local')
        if (targetFolder) form.append('folder_id', targetFolder)
        await api.post(`/teacher/courses/${courseId.value}/question-bank/import`, form, { timeout: 120000 })
        accepted++
      } catch { failed++ }
    }
    folderFiles.value = []
    if (failed) ElMessage.warning(`已本地导入 ${accepted} 个题库，${failed} 个文件需要单独检查`)
    else ElMessage.success(`整包 ${accepted} 个题库已导入，父子目录已保留`)
    await changeCourse()
  } finally { uploading.value = false }
}
async function importWorkbook() {
  if (!courseId.value) return ElMessage.warning('请先选择课程；如果没有课程，请先在教学管理中创建共享课程')
  if (!uploadFile.value) return ElMessage.warning('请先选择 XLS 或 XLSX 题库文件')
  if (useOwnApi.value && aiMode.value === 'auto' && (!aiBaseUrl.value || !aiModel.value || !aiApiKey.value)) {
    return ElMessage.warning('使用教师自有 API 时，请完整填写地址、模型和 API Key')
  }
  const form = new FormData()
  form.append('file', uploadFile.value)
  form.append('ai_mode', aiMode.value)
  form.append('ai_provider', aiProvider.value)
  if (importFolderId.value) form.append('folder_id', importFolderId.value)
  if (useOwnApi.value && aiMode.value === 'auto') {
    form.append('ai_base_url', aiBaseUrl.value)
    form.append('ai_model', aiModel.value)
    form.append('ai_api_key', aiApiKey.value)
  }
  uploading.value = true
  try {
    const result = (await api.post(`/teacher/courses/${courseId.value}/question-bank/import`, form, {
      headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000,
    })).data
    ElMessage.success(result.duplicate
      ? '该文件已导入，本次未重复创建题目'
      : `已识别 ${result.valid_rows} 题，${result.invalid_rows} 行未识别`)
    uploadFile.value = null
    // 自填密钥仅用于本次请求，不在页面状态中继续保留。
    aiApiKey.value = ''
    await changeCourse()
  } catch (error) { fail(error, '题库导入失败') } finally { uploading.value = false }
}
async function createFolder() {
  if (!newFolderName.value.trim()) return ElMessage.warning('请输入文件夹名称')
  try {
    const folder = (await api.post(`/teacher/courses/${courseId.value}/question-folders`, {
      folder_name: newFolderName.value.trim(), folder_type: newFolderType.value,
    })).data
    newFolderName.value = ''
    importFolderId.value = folder.folder_id
    ElMessage.success('题库文件夹已创建')
    await changeCourse()
  } catch (error) { fail(error, '创建文件夹失败') }
}
async function moveItems(itemIds: string[], folderId: string | null) {
  if (!itemIds.length) return ElMessage.warning('请先选择题目')
  try {
    await api.post(`/teacher/courses/${courseId.value}/question-bank/move`, {
      item_ids: itemIds,
      folder_id: folderId,
    })
    ElMessage.success(`已整理 ${itemIds.length} 道题`)
    selectedItems.value = []
    await changeCourse()
  } catch (error) { fail(error, '移动题目失败') }
}
async function moveSelected() {
  await moveItems(selectedItems.value.map(item => item.item_id), moveTargetFolder.value || null)
}
function startQuestionDrag(item: any, event: DragEvent) {
  const selectedIds = selectedItems.value.map(value => value.item_id)
  draggingIds.value = selectedIds.includes(item.item_id) ? selectedIds : [item.item_id]
  event.dataTransfer?.setData('text/plain', draggingIds.value.join(','))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
async function dropQuestions(folderId: string | null) {
  const ids = [...draggingIds.value]
  dragOverFolder.value = ''
  draggingIds.value = []
  await moveItems(ids, folderId)
}
function toggleVisibleSelection(checked: any) {
  if (checked) selectedItems.value = [...filteredItems.value]
  else selectedItems.value = selectedItems.value.filter(item => !filteredItems.value.some(value => value.item_id === item.item_id))
}
function toggleItem(item: any, checked: boolean) {
  selectedItems.value = checked
    ? [...selectedItems.value.filter(value => value.item_id !== item.item_id), item]
    : selectedItems.value.filter(value => value.item_id !== item.item_id)
}
function itemSelectionChanged(item: any, checked: any) {
  toggleItem(item, Boolean(checked))
}
async function bulkApprove() {
  const targets = selectedItems.value.length
    ? selectedItems.value : filteredItems.value.filter(item => item.status === 'draft' && !hasIssue(item))
  const cleanTargets = targets.filter(item => item.status === 'draft' && !hasIssue(item))
  if (!cleanTargets.length) return ElMessage.warning('没有可一键批准的正常题目；异常题请逐题确认')
  try {
    await ElMessageBox.confirm(`确认批量批准 ${cleanTargets.length} 道题？异常题不会自动纳入。`, '一键审核')
    const result = (await api.post(`/teacher/courses/${courseId.value}/question-bank/bulk-review`, {
      item_ids: cleanTargets.map(item => item.item_id),
      status: 'approved',
    })).data
    ElMessage.success(`已批准 ${result.succeeded.length} 道题`)
    if (result.failed.length) ElMessage.warning(`${result.failed.length} 道题仍需人工检查`)
    selectedItems.value = []
    await changeCourse()
  } catch (error: any) { if (error !== 'cancel' && error !== 'close') fail(error, '批量审核失败') }
}
async function save(item: any, status: string) {
  if (item.question_type === 'true_false') {
    item.answer_markdown = String(item.answer_markdown || '').trim().toUpperCase()
  }
  item.correct_answer = item.question_type === 'multiple_choice'
    ? (String(item.answer_markdown || '').toUpperCase().match(/[A-O]/g) || [])
    : item.answer_markdown
  try {
    Object.assign(item, (await api.patch(`/teacher/question-bank/${item.item_id}`, {
      question_type: item.question_type,
      stem_markdown: item.stem_markdown,
      answer_markdown: item.answer_markdown,
      correct_answer: item.correct_answer,
      explanation_markdown: item.explanation_markdown,
      options: item.options,
      difficulty: item.difficulty,
      duration_seconds: item.duration_seconds,
      knowledge_points: item.knowledge_points,
      status,
    })).data)
    ElMessage.success(status === 'approved' ? '题目已批准' : status === 'rejected' ? '题目已驳回' : '草稿已保存')
  } catch (error) { fail(error, '题目审核失败') }
}
async function publish(folderId = importFolderId.value) {
  if (!courseId.value) return ElMessage.warning('请先选择课程')
  try {
    const result = (await api.post(`/teacher/courses/${courseId.value}/question-bank/publish`, null, {
      params: folderId ? { folder_id: folderId } : {},
    })).data
    ElMessage.success(`题库 v${result.version_number} 已发布给学生`)
    await changeCourse()
  } catch (error) { fail(error, '题库发布失败') }
}
async function loadStatistics() {
  if (!courseId.value) {
    statistics.value = null
    return
  }
  loading.value = true
  try {
    statistics.value = (await api.get(
      `/teacher/courses/${courseId.value}/question-bank/statistics`,
      { params: classId.value ? { class_id: classId.value } : {} },
    )).data
  } catch (error) { fail(error, '作答统计加载失败') } finally { loading.value = false }
}
onMounted(loadBase)
</script>

<template>
  <main class="content question-center" v-loading="loading">
    <div class="page-title workbench-hero">
      <span class="eyebrow">REVIEWED QUESTION BANK</span>
      <h1>习题中心</h1>
      <p class="muted">教师导入任意常见 Excel 题库并审核，学生只作答已发布版本；教材例题不会自动进入正式题库。</p>
    </div>
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <el-select v-model="courseId" placeholder="选择课程" @change="changeCourse(true)">
          <el-option v-for="course in courses" :key="course.course_id"
                     :label="course.course_name" :value="course.course_id" />
        </el-select>
        <el-segmented v-model="activeTab" :options="[
          { label: '题库管理', value: 'manage' },
          { label: '学习统计', value: 'statistics' },
        ]" @change="activeTab === 'statistics' && loadStatistics()" />
      </div>
    </el-card>
    <el-alert v-if="!loading && !courses.length" type="warning" :closable="false" show-icon
              title="当前教师账号还没有共享课程，请先在“教学管理”中创建课程，再导入题库。">
      <template #default>
        <el-button type="primary" plain @click="$router.push('/teaching')">前往教学管理</el-button>
      </template>
    </el-alert>

    <template v-if="activeTab === 'manage'">
      <section class="metric-grid workbench-metrics">
        <div class="metric"><span>全部题目</span><strong>{{ summary.total }}</strong></div>
        <div class="metric warning"><span>待审核</span><strong>{{ summary.draft }}</strong></div>
        <div class="metric success"><span>已批准</span><strong>{{ summary.approved }}</strong></div>
        <div class="metric danger"><span>已驳回</span><strong>{{ summary.rejected }}</strong></div>
      </section>
      <el-card shadow="never" class="folder-card">
        <template #header><div class="card-title"><div><h3>试卷 / 作业 / 章节练习</h3><p>每个文件夹独立导入、审核和发布；未归档题目可批量移动。</p></div></div></template>
        <div class="folder-create">
          <el-select v-model="newFolderType">
            <el-option label="新试卷" value="exam"/><el-option label="新作业" value="homework"/>
            <el-option label="新章节练习" value="chapter"/>
          </el-select>
          <el-input v-model="newFolderName" placeholder="输入名称，例如：第三章作业"/>
          <el-button type="primary" @click="createFolder">创建文件夹</el-button>
        </div>
        <div class="organizer-head"><button :class="{active:folderFilter==='all'}" @click="folderFilter='all'">全部题目 <b>{{items.length}}</b></button><button class="unfiled" :class="{active:folderFilter==='unfiled',over:dragOverFolder==='unfiled'}" @click="folderFilter='unfiled'" @dragover.prevent="dragOverFolder='unfiled'" @dragleave="dragOverFolder=''" @drop.prevent="dropQuestions(null)">未归档 <b>{{items.filter(item=>!item.folder_id).length}}</b><small>可拖到这里解除分组</small></button></div>
        <div class="folder-board">
          <section v-for="group in folderGroups" :key="group.type" class="folder-column"><header><b>{{group.title}}</b><span>{{group.subtitle}}</span></header><button v-for="folder in group.folders" :key="folder.folder_id" class="folder-drop" :class="{active:folderFilter===folder.folder_id,over:dragOverFolder===folder.folder_id}" @click="folderFilter=folder.folder_id;importFolderId=folder.folder_id" @dragover.prevent="dragOverFolder=folder.folder_id" @dragleave="dragOverFolder=''" @drop.prevent="dropQuestions(folder.folder_id)"><span>{{folder.folder_name}}</span><b>{{folder.item_count||0}} 题</b><small v-if="folder.issue_count">{{folder.issue_count}} 项待检查</small><small v-else>拖放题目到这里</small></button><div v-if="!group.folders.length" class="empty-folder">创建一个{{group.title}}后可拖入题目</div></section>
        </div>
        <div class="bulk-actions">
          <el-checkbox :model-value="allVisibleSelected" @change="toggleVisibleSelection">全选当前结果</el-checkbox>
          <b v-if="selectedItems.length" class="selected-count">已选 {{selectedItems.length}} 题</b>
          <el-select v-model="moveTargetFolder" clearable placeholder="移动到文件夹；留空为未归档">
            <el-option v-for="folder in folders" :key="folder.folder_id"
                       :label="folder.folder_name" :value="folder.folder_id"/>
          </el-select>
          <el-button :disabled="!selectedItems.length" @click="moveSelected">移动所选（{{selectedItems.length}}）</el-button>
          <el-button type="success" @click="bulkApprove">一键批准正常题目</el-button>
          <el-button type="primary" :disabled="!folderFilter||['all','unfiled'].includes(folderFilter)"
                     @click="publish(folderFilter)">统一发布当前文件夹</el-button>
        </div>
      </el-card>
      <el-card shadow="never" class="import-card">
        <template #header>
          <div class="card-title">
            <div><h3>导入 Excel 题库</h3><p>无需固定列号或固定首行，系统会扫描所有工作表并按内容识别字段。</p></div>
            <el-select v-model="importFolderId" clearable placeholder="选择导入目标文件夹">
              <el-option v-for="folder in folders" :key="folder.folder_id"
                         :label="folder.folder_name" :value="folder.folder_id"/>
            </el-select>
          </div>
        </template>
        <div class="upload-row">
          <el-upload :auto-upload="false" :limit="1" accept=".xls,.xlsx"
                     :on-change="chooseUpload" :show-file-list="true">
            <el-button :disabled="!courseId">选择 XLS / XLSX 文件</el-button>
          </el-upload>
          <el-button type="primary" :loading="uploading"
                     :disabled="!courseId || !uploadFile" @click="importWorkbook">导入并进入审核</el-button>
          <label class="folder-picker"><input type="file" multiple webkitdirectory accept=".xls,.xlsx" @change="chooseFolderUpload"/>选择整个题库文件夹</label>
          <el-button type="success" plain :loading="uploading" :disabled="!folderFiles.length" @click="importFolderPackage">整包本地导入（{{folderFiles.length}}）</el-button>
        </div>
        <el-collapse class="ai-settings">
          <el-collapse-item title="智能识别与教师自有 API（可选）">
            <el-alert type="info" :closable="false"
              title="本地规则先识别；只有低置信度或非标准内容才调用 API。API 失败不会丢失本地结果。" />
            <div class="ai-grid">
              <div><label>识别方式</label><el-radio-group v-model="aiMode">
                <el-radio-button label="auto">自动：本地 + AI 补充</el-radio-button>
                <el-radio-button label="local">仅本地识别</el-radio-button>
              </el-radio-group></div>
              <div><label>接口来源</label><el-switch v-model="useOwnApi" active-text="使用我自己的 API" inactive-text="使用服务器默认接口" /></div>
              <template v-if="useOwnApi && aiMode === 'auto'">
                <div><label>协议</label><el-select v-model="aiProvider">
                  <el-option label="OpenAI 兼容接口" value="openai_compatible" />
                  <el-option label="Google Gemini" value="gemini" />
                </el-select></div>
                <div><label>Base URL</label><el-input v-model="aiBaseUrl" placeholder="https://example.com/v1" /></div>
                <div><label>模型</label><el-input v-model="aiModel" placeholder="qwen-plus / gemini-2.5-flash" /></div>
                <div><label>API Key</label><el-input v-model="aiApiKey" type="password" show-password autocomplete="off" /></div>
              </template>
            </div>
            <p class="muted">自有 API Key 仅用于本次导入请求，不保存到数据库，也不会显示在导入记录中。</p>
          </el-collapse-item>
        </el-collapse>
        <el-collapse v-if="imports.length" class="import-history">
          <el-collapse-item v-for="entry in imports" :key="entry.import_id">
            <template #title>
              <span>{{ entry.original_name }}</span>
              <el-tag type="success">{{ entry.valid_rows }} 题有效</el-tag>
              <el-tag v-if="entry.invalid_rows" type="warning">{{ entry.invalid_rows }} 行异常</el-tag>
              <el-tag :type="entry.ai_used ? 'primary' : 'info'">{{ entry.ai_used ? 'AI 辅助' : '本地识别' }}</el-tag>
              <span class="muted">{{ entry.created_at }}</span>
            </template>
            <el-alert v-for="warning in entry.warnings" :key="`${warning.sheet}-${warning.row}-${warning.message}`"
                      type="warning" :closable="false" show-icon
                      :title="`${warning.sheet || '导入'}${warning.row ? ` 第 ${warning.row} 行` : ''}：${warning.message}`" />
            <el-table v-if="entry.errors.length" :data="entry.errors" size="small">
              <el-table-column prop="sheet" label="工作表" width="120" />
              <el-table-column prop="row" label="Excel 行号" width="110" />
              <el-table-column prop="message" label="未导入原因" />
            </el-table>
            <el-empty v-else description="所有数据行均通过校验" :image-size="48" />
          </el-collapse-item>
        </el-collapse>
      </el-card>
      <el-radio-group v-model="statusFilter">
        <el-radio-button label="all">全部</el-radio-button>
        <el-radio-button label="draft">待审核</el-radio-button>
        <el-radio-button label="approved">已批准</el-radio-button>
        <el-radio-button label="rejected">已驳回</el-radio-button>
      </el-radio-group>
      <el-empty v-if="!filteredItems.length" description="暂无符合条件的题目，请先导入题库模板" />
      <el-card v-for="(item, index) in filteredItems" :key="item.item_id" shadow="never" class="question-card" :class="{selected:selectedItems.some(value=>value.item_id===item.item_id)}">
        <div class="question-head">
          <span class="drag-handle" draggable="true" title="拖到上方试卷、作业或章节练习" @dragstart="startQuestionDrag(item,$event)">⠿</span>
          <el-checkbox :model-value="selectedItems.some(value=>value.item_id===item.item_id)"
                       @change="itemSelectionChanged(item,$event)"/>
          <span class="question-index">{{ index + 1 }}</span>
          <el-tag v-if="hasIssue(item)" type="danger">导入异常 · 优先审批</el-tag>
          <el-select v-model="item.question_type" class="type-select">
            <el-option label="单选题" value="single_choice" />
            <el-option label="多选题" value="multiple_choice" />
            <el-option label="判断题" value="true_false" />
            <el-option label="简答题" value="short_answer" />
          </el-select>
          <el-tag :type="item.status === 'approved' ? 'success' : item.status === 'rejected' ? 'danger' : 'warning'">
            {{ item.status === 'approved' ? '已批准' : item.status === 'rejected' ? '已驳回' : '待审核' }}
          </el-tag>
          <span class="muted">Excel 第 {{ item.import_row_number }} 行</span>
          <el-tag v-if="item.recognition_method" effect="plain">
            {{ item.recognition_method === 'local' ? '本地识别' : item.recognition_method === 'ai' ? 'AI 识别' : '本地 + AI' }}
            · {{ Math.round((item.recognition_confidence || 0) * 100) }}%
          </el-tag>
        </div>
        <el-alert v-if="item.recognition_notes?.length" type="warning" :closable="false"
                  :title="item.recognition_notes.join('；')" />
        <label>题干</label>
        <el-input v-model="item.stem_markdown" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }" />
        <div v-if="item.options?.length" class="options-editor">
          <div v-for="option in item.options" :key="option.key" class="option-row">
            <strong>{{ option.key }}</strong><el-input v-model="option.text" />
          </div>
        </div>
        <div class="answer-grid">
          <div><label>标准答案</label>
            <el-select v-if="item.question_type === 'true_false'" v-model="item.answer_markdown"
                       placeholder="选择判断答案">
              <el-option v-for="choice in judgeChoices(item)" :key="choice"
                         :label="choice" :value="choice" />
            </el-select>
            <el-input v-else v-model="item.answer_markdown" />
          </div>
          <div><label>难度</label><el-input v-model="item.difficulty" /></div>
          <div><label>建议用时（秒）</label><el-input-number v-model="item.duration_seconds" :min="1" /></div>
        </div>
        <label>答案解析</label>
        <el-input v-model="item.explanation_markdown" type="textarea"
                  :autosize="{ minRows: 2, maxRows: 8 }" placeholder="可补充解析，帮助学生订正" />
        <div class="question-actions">
          <el-button type="success" @click="save(item, 'approved')">批准</el-button>
          <el-button type="danger" plain @click="save(item, 'rejected')">驳回</el-button>
          <el-button @click="save(item, 'draft')">保存草稿</el-button>
        </div>
      </el-card>
    </template>

    <template v-else>
      <el-card shadow="never" class="stats-filter">
        <el-select v-model="classId" clearable placeholder="全部已授权学生">
          <el-option v-for="entry in classes" :key="entry.class_id"
                     :label="`${entry.class_name}（${entry.member_count} 人）`" :value="entry.class_id" />
        </el-select>
        <el-button type="primary" @click="loadStatistics">刷新统计</el-button>
        <span class="muted">按每名学生对每道题最近一次作答统计。</span>
      </el-card>
      <template v-if="statistics">
        <section class="metric-grid">
          <div class="metric"><span>学生人数</span><strong>{{ statistics.summary.students }}</strong></div>
          <div class="metric"><span>已参与学生</span><strong>{{ statistics.summary.answered }}</strong></div>
          <div class="metric"><span>有效作答</span><strong>{{ statistics.summary.attempts }}</strong></div>
          <div class="metric success"><span>总体正确率</span><strong>{{ statistics.summary.accuracy }}%</strong></div>
        </section>
        <el-card shadow="never" class="stats-card">
          <template #header><h3>高错误率题目排行榜</h3></template>
          <el-table :data="statistics.ranking" stripe>
            <el-table-column prop="rank" label="排名" width="72" />
            <el-table-column prop="stem_markdown" label="题目" min-width="360" show-overflow-tooltip />
            <el-table-column prop="attempts" label="作答人数" width="100" />
            <el-table-column prop="wrong_count" label="答错人数" width="100" />
            <el-table-column label="错误率" width="180">
              <template #default="{ row }">
                <el-progress :percentage="row.error_rate"
                             :color="row.error_rate >= 60 ? '#ef4444' : row.error_rate >= 30 ? '#f59e0b' : '#22c55e'" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        <el-card shadow="never" class="stats-card">
          <template #header><h3>每名学生的错题</h3></template>
          <el-table :data="statistics.students" stripe>
            <el-table-column type="expand">
              <template #default="{ row }">
                <el-table v-if="row.wrong_questions.length" :data="row.wrong_questions" size="small">
                  <el-table-column prop="question" label="答错题目" min-width="320" />
                  <el-table-column prop="response" label="学生答案" width="150" />
                  <el-table-column prop="correct_answer" label="正确答案" width="150" />
                </el-table>
                <el-empty v-else description="暂无错题" :image-size="48" />
              </template>
            </el-table-column>
            <el-table-column prop="student_number" label="学号" width="150" />
            <el-table-column prop="display_name" label="姓名" min-width="130" />
            <el-table-column prop="answered" label="已答题" width="90" />
            <el-table-column prop="wrong_count" label="错题数" width="90" />
            <el-table-column label="正确率" width="110">
              <template #default="{ row }">{{ row.accuracy }}%</template>
            </el-table-column>
          </el-table>
        </el-card>
      </template>
      <el-empty v-else description="请选择范围并刷新统计" />
    </template>
  </main>
</template>

<style scoped>
.question-center{display:grid;gap:18px}.toolbar-card,.import-card,.question-card,.stats-card,.stats-filter{border-radius:16px}
.toolbar,.card-title,.upload-row,.question-head,.question-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.toolbar,.card-title{justify-content:space-between}.card-title h3,.card-title p,.stats-card h3{margin:0}
.card-title p{margin-top:5px;color:#687d77}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.metric{padding:18px;background:#f2faf8;border:1px solid #dce9e5;border-radius:14px}.metric span{display:block;color:#687d77;font-size:13px}
.metric strong{display:block;margin-top:8px;font-size:30px;color:#173e49}.metric.success strong{color:#23746f}
.metric.warning strong{color:#b45309}.metric.danger strong{color:#b91c1c}.import-history{margin-top:18px}
.ai-settings{margin-top:18px}.ai-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}
.folder-create,.folder-list,.bulk-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.folder-create .el-input{max-width:420px}.folder-create .el-select,.bulk-actions .el-select{width:220px}.folder-list{margin:14px 0}.folder-card{border-radius:16px}
.ai-grid label{display:block;margin-bottom:6px;color:#47655e;font-size:13px;font-weight:600}.ai-grid .el-select{width:100%}
.import-history :deep(.el-collapse-item__title){gap:10px}.question-card{border-left:4px solid #378f81}
.question-card label{display:block;margin:14px 0 6px;color:#47655e;font-size:13px;font-weight:600}
.question-index{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:#d9eee8;color:#23746f;font-weight:700}
.type-select{width:130px}.options-editor{margin-top:12px;display:grid;gap:8px}.option-row{display:grid;grid-template-columns:28px 1fr;align-items:center;gap:8px}
.answer-grid{display:grid;grid-template-columns:1.4fr .7fr .7fr;gap:12px}.question-actions{justify-content:flex-end;margin-top:16px}
.stats-filter :deep(.el-card__body){display:flex;align-items:center;gap:12px;flex-wrap:wrap}.muted{color:#687d77;font-size:13px}
.question-center{background:#f3f7f7;min-height:100vh}.question-center :deep(.el-button--primary){--el-button-bg-color:#23746f;--el-button-border-color:#23746f;--el-button-hover-bg-color:#378f81;--el-button-hover-border-color:#378f81}.question-center :deep(.el-segmented__item-selected){color:#173e49;background:#dcefe9}.folder-card,.import-card,.stats-filter{border-color:#dce9e5}
.organizer-head{display:flex;gap:10px;margin:15px 0 12px}.organizer-head button{display:grid;grid-template-columns:1fr auto;gap:4px 14px;min-width:160px;padding:11px 14px;border:1px solid #d4e3df;border-radius:11px;background:#fff;color:#365b55;text-align:left;cursor:pointer}.organizer-head button small{grid-column:1/-1;color:#81938f}.organizer-head button.active{border-color:#378f81;background:#eaf6f2;color:#173e49}.folder-board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:14px}.folder-column{display:grid;align-content:start;gap:8px;min-height:142px;padding:12px;border:1px solid #dbe8e5;border-radius:13px;background:#f7fbfa}.folder-column>header{display:grid;margin-bottom:2px}.folder-column>header b{color:#173e49}.folder-column>header span{font-size:12px;color:#7b8e89}.folder-drop{display:grid;grid-template-columns:1fr auto;gap:5px 10px;padding:11px;border:1px solid #dce8e5;border-radius:10px;background:#fff;color:#365b55;text-align:left;cursor:pointer;transition:.16s ease}.folder-drop small{grid-column:1/-1;color:#84948f}.folder-drop.active{border-color:#378f81;background:#edf8f5}.folder-drop.over,.organizer-head button.over{border-color:#23746f;background:#dcefe9;box-shadow:0 0 0 3px #378f8126;transform:translateY(-2px)}.empty-folder{padding:17px 8px;border:1px dashed #c8dbd6;border-radius:9px;color:#879792;text-align:center;font-size:12px}.selected-count{padding:6px 10px;border-radius:9px;background:#dcefe9;color:#173e49}.drag-handle{display:grid;place-items:center;width:26px;height:30px;border-radius:7px;color:#5e7c75;font-size:22px;cursor:grab;user-select:none}.drag-handle:active{cursor:grabbing}.question-card.selected{border-color:#378f81;background:#fbfefd;box-shadow:0 0 0 2px #378f811c}
@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.answer-grid,.ai-grid{grid-template-columns:1fr}}
@media(max-width:900px){.folder-board{grid-template-columns:1fr}.organizer-head{flex-wrap:wrap}}
</style>
