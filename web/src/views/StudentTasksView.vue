<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { saveStudentDraft, readStudentDraft } from '../student-navigation'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import PaperWorkspace from '../components/PaperWorkspace.vue'
import { dateLabel } from '../portrait-utils'
const scopes = ref<any[]>([]), scopeId = ref(''), tasks = ref<any[]>([]), task = ref<any>(null)
const answers = ref<Record<string, any>>({}), loading = ref(false), submitting = ref(false), error = ref('')
const scope = computed(() => scopes.value.find(s => s.class_id === scopeId.value))
const route = useRoute()
const owner = useAuthStore().user?.user_id || ''
const hasDraft = ref(false)
let restoring = false
const draftKey = (row: any) => `formal-task:${scope.value?.course_id}:${scopeId.value}:${row.task_id}`
const revision = (row: any) => JSON.stringify([row.items, row.submissions])
function saveDraft() {
  if (task.value && hasDraft.value) saveStudentDraft(owner, draftKey(task.value), { answers: answers.value, revision: revision(task.value), requestId, sentPayload })
}
watch(answers, () => { if (!restoring && task.value) { hasDraft.value = true; saveDraft() } }, {deep:true,flush:'sync'})
async function confirmLeave() {
  if (submitting.value) { ElMessage.info('正在提交，请稍候'); return false }
  if (!hasDraft.value) return true
  try {
    await ElMessageBox.confirm('答案尚未提交。离开后可在本次页面会话中恢复草稿；刷新或关闭网页会丢失草稿。', '保留草稿并离开？', {confirmButtonText:'保留并离开',cancelButtonText:'继续作答',type:'warning'})
    saveDraft(); return true
  } catch { return false }
}
async function changeScope(id: string) { if (id !== scopeId.value && await confirmLeave()) { scopeId.value = id; await load() } }
async function chooseTask(row: any) { if (row.task_id !== task.value?.task_id && await confirmLeave()) { select(row); await nextTick(); document.querySelector('.paper-workspace')?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'}) } }
function protectUnload(event: BeforeUnloadEvent) { if (hasDraft.value || submitting.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(confirmLeave)
let requestId = '', sentPayload = '', epoch = 0, disposed = false
const closed = computed(() => Boolean(task.value && (
  task.value.remaining_submissions === 0 ||
  (task.value.kind === 'exam' && new Date(task.value.due_at).getTime() < Date.now())
)))
const submissionLimit = (row: any) => row.max_submissions == null ? '不限次数' : `最多 ${row.max_submissions} 次`
const answered = computed(() => Object.values(answers.value).filter(a => Array.isArray(a) ? a.length : a != null && a !== '').length)
function options(q: any): {key: string; text: string}[] {
  if (q.question_type === 'true_false') return [{key:'Y',text:'正确'},{key:'N',text:'错误'}]
  return Array.isArray(q.options) ? q.options.map((o: any,i: number) => typeof o === 'string' ? {key:String.fromCharCode(65+i),text:o} : o) : Object.entries(q.options).map(([key,text])=>({key,text:String(text)}))
}
const message = (e: any) => typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '请求失败，请重试'
async function load() {
  const id = ++epoch; task.value = null; hasDraft.value = false; tasks.value = []; error.value = ''
  if (!scope.value) return
  loading.value = true
  try {
    const result = await api.get(`/student/courses/${scope.value.course_id}/classes/${scopeId.value}/tasks`)
    if (!disposed && id === epoch) tasks.value = result.data
  } catch (e) { if (id === epoch) error.value = message(e) } finally { if (id === epoch) loading.value = false }
}
function select(row: any) {
  restoring = true; task.value = row
  const draft = readStudentDraft(owner, draftKey(row))
  const restore = draft?.revision === revision(row) && !closed.value
  answers.value = restore ? draft.answers : Object.fromEntries(row.items.map((q: any)=>[q.item_id,q.question_type==='multiple_choice'?[]:'']))
  requestId = restore ? draft.requestId : ''; sentPayload = restore ? draft.sentPayload : ''
  hasDraft.value = Boolean(restore); restoring = false
  if (restore) ElMessage.success('已恢复本次会话中的作答草稿')
}
async function submit() {
  if (!task.value || submitting.value) return
  submitting.value = true
  if (task.value.kind === 'exam') {
    try { await ElMessageBox.confirm(`已回答 ${answered.value}/${task.value.items.length} 题。${submissionLimit(task.value)}，本次提交会占用一次，确认提交？`, '提交考试', {type:'warning'}) } catch { submitting.value = false; return }
  }
  const selectedId = task.value.task_id
  const responses = JSON.stringify(answers.value)
  // Retry the exact same request after a network failure, including one-shot exams.
  if (!requestId || sentPayload !== responses) { requestId = crypto.randomUUID(); sentPayload = responses }
  saveDraft()
  try {
    const result = await api.post(`/student/tasks/${selectedId}/submissions`, {request_id:requestId,responses:JSON.parse(responses)})
    saveStudentDraft(owner, draftKey(task.value), null); hasDraft.value = false
    ElMessage.success(`${result.data.late?'补交':'提交'}成功，${Number(result.data.score).toFixed(1)} 分${result.data.complete?'':'（尚未完整作答）'}`)
    await load(); const updated = tasks.value.find(t=>t.task_id===selectedId); if (updated) select(updated)
  } catch (e) { ElMessage.error(message(e)) } finally { submitting.value = false }
}
onMounted(async () => {
  window.addEventListener('beforeunload', protectUnload)
  try { const result = await api.get('/student/task-scopes'); if(disposed)return; scopes.value=result.data; scopeId.value=scopes.value.find((s:any)=>s.class_id===route.query.class_id)?.class_id || scopes.value[0]?.class_id || ''; await load(); const target=tasks.value.find(t=>t.task_id===route.query.task_id); if(target){select(target); await nextTick(); document.querySelector('.paper-workspace')?.scrollIntoView({block:'start'})} }
  catch(e){error.value=message(e)}
})
onUnmounted(()=>{saveDraft();window.removeEventListener('beforeunload',protectUnload);disposed=true;epoch++})
</script>
<template>
  <main class="content task-page">
    <header class="page-title task-page-header">
      <div class="task-page-intro">
        <h1>班级作业与考试</h1>
        <p>每次提交均立即显示分数；可提交次数以教师发布的任务设置为准。</p>
      </div>
      <nav class="task-page-actions" aria-label="页面快捷入口">
        <el-button @click="$router.push('/student/courses')">返回课程</el-button>
        <el-button @click="$router.push('/student/study-room')">自习室</el-button>
      </nav>
    </header>
    <div class="form-field"><label for="task-scope">课程与教学班</label><el-select id="task-scope" :model-value="scopeId" placeholder="选择课程与班级" :disabled="submitting" @change="changeScope"><el-option v-for="s in scopes" :key="s.class_id" :value="s.class_id" :label="`${s.course_name} · ${s.class_name}`"/></el-select></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"/>
    <el-empty v-if="!scopes.length" description="暂无已加入的共享课程教学班"/>
    <el-table :data="tasks" v-loading="loading" empty-text="当前班级暂无正式任务"><el-table-column prop="title" label="任务"/><el-table-column label="类型"><template #default="{row}">{{row.kind==='exam'?'考试':'作业'}}</template></el-table-column><el-table-column label="截止时间"><template #default="{row}">{{dateLabel(row.due_at)}}</template></el-table-column><el-table-column label="提交次数"><template #default="{row}">{{row.submission_count}} / {{row.max_submissions ?? '不限'}}</template></el-table-column><el-table-column label="操作"><template #default="{row}"><el-button :disabled="submitting" @click="chooseTask(row)">查看与作答</el-button></template></el-table-column></el-table>
    <template v-if="task">
      <el-alert v-if="closed" :title="task.remaining_submissions===0?'提交次数已用尽。':'考试已截止。'" type="info" :closable="false"/>
      <PaperWorkspace :key="task.task_id" :title="task.title" :subtitle="`截止 ${dateLabel(task.due_at)} · ${submissionLimit(task)}`"
        :items="task.items" :answered="task.items.map((q:any)=>Array.isArray(answers[q.item_id]) ? answers[q.item_id].length>0 : answers[q.item_id]!=null && answers[q.item_id]!=='')" :disabled="closed || submitting">
        <template #answer="{item:q}">
          <el-checkbox-group v-if="q.question_type==='multiple_choice'" v-model="answers[q.item_id]" :disabled="closed || submitting"><el-checkbox v-for="o in options(q)" :key="o.key" :value="o.key">{{o.key}}. {{o.text}}</el-checkbox></el-checkbox-group>
          <el-radio-group v-else v-model="answers[q.item_id]" :disabled="closed || submitting"><el-radio v-for="o in options(q)" :key="o.key" :value="o.key">{{o.key}}. {{o.text}}</el-radio></el-radio-group>
        </template>
        <template #submit><el-button type="primary" :loading="submitting" :disabled="closed || !task.items.length" @click="submit">正式提交{{task.kind==='exam'?'考试':'作业'}}</el-button></template>
        <template #notice><p v-if="hasDraft" class="paper-notice" role="status">未提交，草稿暂存于本次页面会话。刷新或关闭网页会丢失草稿。</p></template>
      </PaperWorkspace>
      <el-collapse class="submission-history"><el-collapse-item title="我的提交记录" name="history"><el-table :data="task.submissions" empty-text="尚未提交"><el-table-column label="提交时间"><template #default="{row}">{{dateLabel(row.submitted_at)}}</template></el-table-column><el-table-column label="成绩（百分制）" prop="score"/><el-table-column label="作答完整"><template #default="{row}">{{row.answered}}/{{task.items.length}}</template></el-table-column></el-table></el-collapse-item></el-collapse>
    </template>
  </main>
</template>
<style scoped>
.task-page{display:grid;gap:24px;max-width:1440px;margin:auto;padding:32px}
.task-page-header{display:flex;align-items:center;justify-content:space-between;gap:24px;min-width:0}
.task-page-intro{min-width:0}
.task-page-header h1{font-size:28px;margin:0 0 8px}
.task-page-header p{color:#56634d;font-size:14px;line-height:1.6;margin:0}
.task-page-actions{display:flex;align-items:center;gap:12px;flex:none}
.task-page-actions .el-button{min-width:104px;margin:0}
.form-field{max-width:460px}.task-page>.el-table{border:1px solid #dce5df;border-radius:12px}.submission-history{padding:4px 22px;background:#fff;border:1px solid #dce5df;border-radius:12px}.submission-history :deep(.el-collapse-item__header){font-size:15px;font-weight:600}.submission-history :deep(.el-collapse-item__wrap){border-bottom:0}
@media(max-width:760px){.task-page-header{align-items:flex-start;flex-direction:column;gap:20px}.task-page-actions{flex-wrap:wrap;gap:10px}}
</style>
