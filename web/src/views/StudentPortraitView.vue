<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import ExpandableList from '../components/ExpandableList.vue'
import { dateLabel, localDateRange, percentage, taskRank } from '../portrait-utils'

const route = useRoute()
const courses = ref<any[]>([]), classes = ref<any[]>([]), terms = ref<any[]>([])
const courseId = ref(''), classId = ref(''), studentId = ref(''), rangeMode = ref('recent')
const range = ref<[string, string]>(['', ''])
const students = ref<any[]>([]), portrait = ref<any>(null), tasks = ref<any[]>([]), sources = ref<any[]>([])
const loading = ref(false), publishing = ref(false), evaluating = ref(false), error = ref('')
const activeTab = ref('portraits'), title = ref(''), kind = ref('homework'), due = ref(''), versionId = ref('')
const maxSubmissions = ref<number | 'unlimited'>(1)
const selectedItems = ref<any[]>([])
const publishAttempted = ref(false)
let epoch = 0, detailEpoch = 0, timer: number | undefined, disposed = false
const prefix = computed(() => `/teacher/courses/${courseId.value}/classes/${classId.value}`)
const metrics = computed(() => portrait.value?.metrics)
const evaluationLabels: Record<string, string> = { overview: '表现概述', strengths: '优势', improvements: '待巩固点', suggestions: '教学建议', limitations: '数据局限' }
const name = computed(() => { const row = students.value.find(s => s.user_id === studentId.value); return row?.display_name || row?.username || '' })
const sourceItems = computed(() => sources.value.find(s => s.version_id === versionId.value)?.items || [])
const points = ref<Record<string, number>>({})
const msg = (e: any) => typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : e?.message || '加载失败，请重试'
function ymd(d: Date) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` }
function setRange() {
  if (rangeMode.value === 'custom') return true
  const now = new Date(), start = new Date(now)
  if (rangeMode.value === 'term') {
    const term = terms.value.find(t => t.term_id === classes.value.find(c => c.class_id === classId.value)?.term_id)
    if (!term?.starts_on || !term?.ends_on) { error.value = '该学期未设置起止日期，请使用自定义日期。'; rangeMode.value = 'custom'; return false }
    range.value = [term.starts_on, term.ends_on]
  } else {
    start.setDate(start.getDate()-29)
    range.value = [ymd(start), ymd(now)]
  }
  return true
}
async function changeRange() { if (setRange()) await loadClass() }
async function loadBase() {
  loading.value = true; error.value = ''
  try {
    const results = await Promise.all([api.get('/teacher/courses'), api.get('/teacher/terms')])
    if (disposed) return
    courses.value = results[0].data; terms.value = results[1].data
    courseId.value = courses.value.find(c => c.course_id === route.query.course_id)?.course_id || courses.value[0]?.course_id || ''
    await changeCourse()
  } catch (e) { error.value = msg(e) } finally { loading.value = false }
}
async function changeCourse() {
  const id = ++epoch; detailEpoch++; portrait.value = null; students.value = []; classes.value = []; classId.value = ''; studentId.value = ''; tasks.value = []; sources.value = []; versionId.value = ''
  if (!courseId.value) return
  loading.value = true; error.value = ''
  try {
    const result = await api.get('/teacher/classes', { params: { course_id: courseId.value } })
    if (id !== epoch || disposed) return
    classes.value = result.data.filter((c: any) => c.status === 'active')
    classId.value = classes.value.find(c => c.class_id === route.query.class_id)?.class_id || classes.value[0]?.class_id || ''
    if (setRange()) await loadClass()
  } catch (e) { if (id === epoch) error.value = msg(e) } finally { if (id === epoch) loading.value = false }
}
async function loadClass() {
  const id = ++epoch; detailEpoch++; portrait.value = null; students.value = []; tasks.value = []; sources.value = []; selectedItems.value = []; versionId.value = ''
  if (!classId.value) { loading.value = false; return }
  loading.value = true; error.value = ''
  try {
    const params = localDateRange(...range.value)
    const results = await Promise.all([api.get(`${prefix.value}/portraits`, { params }), api.get(`${prefix.value}/tasks`), api.get(`${prefix.value}/task-sources`)])
    if (id !== epoch || disposed) return
    students.value = results[0].data; tasks.value = results[1].data; sources.value = results[2].data
    if (!students.value.some(s => s.user_id === studentId.value)) studentId.value = students.value[0]?.user_id || ''
    await loadPortrait()
  } catch (e) { if (id === epoch) error.value = msg(e) } finally { if (id === epoch) loading.value = false }
}
async function loadPortrait(quiet = false) {
  const id = ++detailEpoch, parent = epoch
  if (!quiet) portrait.value = null
  if (!studentId.value || !classId.value) return
  try {
    const result = await api.get(`${prefix.value}/portraits/${studentId.value}`, { params: localDateRange(...range.value) })
    if (id === detailEpoch && parent === epoch && !disposed) portrait.value = result.data
  } catch (e) { if (id === detailEpoch && parent === epoch) { portrait.value = null; error.value = msg(e) } }
}
async function evaluate() {
  const id = detailEpoch, parent = epoch
  evaluating.value = true
  try {
    const result = await api.post(`${prefix.value}/portraits/${studentId.value}/evaluate`, localDateRange(...range.value), { timeout: 180000 })
    if (id !== detailEpoch || parent !== epoch || disposed) return
    portrait.value = result.data.portrait
    if (result.data.status !== 'draft') ElMessage.warning(result.data.message)
  } catch (e) { ElMessage.error(msg(e)) } finally { evaluating.value = false }
}
async function publish() {
  publishAttempted.value = true
  if (!title.value.trim() || !versionId.value || !due.value || !selectedItems.value.length) { ElMessage.warning('请填写任务名称、题库、题目和截止时间'); return }
  publishing.value = true
  try {
    await api.post(`${prefix.value}/tasks`, { title: title.value, kind: kind.value, max_submissions: maxSubmissions.value === 'unlimited' ? null : maxSubmissions.value, version_id: versionId.value, due_at: new Date(due.value).toISOString(), items: selectedItems.value.map(q => ({ item_id: q.item_id, points: points.value[q.item_id] ?? 1 })) })
    publishAttempted.value = false; ElMessage.success('任务已发布，试题与应完成人员已固定'); title.value = ''; maxSubmissions.value = 1; selectedItems.value = []; await loadClass()
  } catch (e) { ElMessage.error(msg(e)) } finally { publishing.value = false }
}
const chartSeries = computed(() => ['homework', 'exam'].map(type => {
  const all = (portrait.value?.tasks || []).filter((t: any) => t.score != null)
  const dates = all.map((t: any) => new Date(t.due_at).getTime())
  const min = Math.min(...dates), span = Math.max(1, Math.max(...dates)-min)
  return { type, label: type === 'homework' ? '作业' : '考试', color: type === 'homework' ? '#294b3c' : '#95652f',
    points: all.filter((t: any) => t.kind === type).map((t: any) => ({ x: 40+((new Date(t.due_at).getTime()-min)/span)*660, y: 180-t.score*1.5, title: `${t.title}：${t.score} 分（${dateLabel(t.due_at)}）` })) }
}))
onMounted(() => { setRange(); void loadBase(); timer = window.setInterval(() => { if (!evaluating.value && !loading.value && document.visibilityState === 'visible') void loadPortrait(true) }, 30000) })
onUnmounted(() => { disposed = true; epoch++; detailEpoch++; window.clearInterval(timer) })
</script>

<template>
  <main class="content portrait-page" :aria-busy="loading">
    <header class="page-title"><span class="eyebrow">班级 · 学生画像</span><h1>看见学习变化，让建议有依据</h1><p class="muted">分别观察及时性、作答质量、成绩与授权自习表现，不合成总分。</p></header>
    <el-card shadow="never"><div class="filters">
      <el-select v-model="courseId" placeholder="课程" :disabled="loading || publishing" @change="changeCourse"><el-option v-for="c in courses" :key="c.course_id" :label="c.course_name" :value="c.course_id" /></el-select>
      <el-select v-model="classId" placeholder="教学班" :disabled="loading || publishing" @change="changeRange"><el-option v-for="c in classes" :key="c.class_id" :label="c.class_name" :value="c.class_id" /></el-select>
      <el-select v-model="rangeMode" aria-label="统计周期" :disabled="loading" @change="changeRange"><el-option label="最近 30 天" value="recent"/><el-option label="本学期" value="term"/><el-option label="自定义" value="custom"/></el-select>
      <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" :disabled="loading" @change="rangeMode='custom'; loadClass()" />
      <el-button :loading="loading" @click="classId ? loadClass() : loadBase()">刷新</el-button>
    </div></el-card>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-empty v-if="!classId && !loading" description="请先创建课程、教学班并添加学生" />
    <el-tabs v-if="classId" v-model="activeTab">
      <el-tab-pane label="学生画像" name="portraits">
        <div class="portrait-layout">
          <aside><el-card shadow="never"><h2>班级学生 <small>{{students.length}} 人</small></h2><el-empty v-if="!students.length" description="暂无学生"/><ExpandableList :items="students" :limit="8" label="班级学生" :reset-key="courseId + ':' + classId"><template #default="{items:visibleItems}"><button v-for="s in visibleItems" :key="s.user_id" class="student-row" :class="{active: studentId===s.user_id}" @click="studentId=s.user_id; loadPortrait()"><b>{{s.display_name || s.username}}</b><small>{{s.username}} · 到期完成率 {{percentage(s.summary.completion_rate)}}</small></button></template></ExpandableList></el-card></aside>
          <section v-if="portrait && metrics" class="portrait-detail">
            <h2>{{name}} <small class="muted">仅当前课程与班级</small></h2>
            <div class="metric-grid">
              <el-card shadow="never"><span>到期任务完成率</span><strong>{{percentage(metrics.completion_rate)}}</strong><small>{{metrics.completed_tasks}} / {{metrics.due_tasks}} 项，含完整补交</small></el-card>
              <el-card shadow="never"><span>按时完整提交率</span><strong>{{percentage(metrics.on_time_rate)}}</strong><small>按首次完整提交判断</small></el-card>
              <el-card shadow="never"><span>答题完整率</span><strong>{{percentage(metrics.answer_completeness)}}</strong><small>所列任务最近一次提交</small></el-card>
              <el-card v-for="t in ['homework','exam']" :key="t" shadow="never"><span>{{t==='homework'?'作业':'考试'}}平均分</span><strong>{{metrics[t].average_score ?? '暂无成绩'}}</strong><small>{{metrics[t].count}} 次已评分任务 · 百分制</small></el-card>
              <el-card shadow="never"><span>专注参考值</span><strong>{{metrics.study.authorized ? percentage(metrics.study.focus_reference) : '未授权'}}</strong><small>有效采样 {{(metrics.study.valid_sample_seconds/60).toFixed(1)}} 分钟</small></el-card>
            </div>
            <el-card shadow="never"><h3>成绩变化</h3>
              <svg v-if="chartSeries.some(s=>s.points.length)" class="score-chart" viewBox="0 0 740 210" role="img" aria-label="按任务截止日期展示作业与考试百分制成绩，详细值见下方任务表">
                <g v-for="score in [0,50,100]" :key="score"><line x1="40" x2="720" :y1="180-score*1.5" :y2="180-score*1.5" stroke="#dce1d4"/><text x="4" :y="185-score*1.5" fill="#56634d">{{score}}</text></g>
                <g v-for="s in chartSeries" :key="s.type"><polyline :points="s.points.map((p: {x:number;y:number})=>`${p.x},${p.y}`).join(' ')" fill="none" :stroke="s.color" stroke-width="2"/><circle v-for="(p,i) in s.points" :key="i" :cx="p.x" :cy="p.y" r="4" :fill="s.color"><title>{{p.title}}</title></circle></g>
                <text x="40" y="205" fill="#294b3c">● 作业</text><text x="130" y="205" fill="#95652f">● 考试</text><text x="480" y="205" fill="#56634d">横轴：任务截止日期 →</text>
              </svg><el-empty v-else description="暂无已评分任务"/>
              <p v-for="t in ['homework','exam']" :key="t">{{t==='homework'?'作业':'考试'}}：{{portrait.trends[t].change == null ? '样本不足，暂不判断趋势' : `较前一等长时段变化 ${portrait.trends[t].change > 0 ? '+' : ''}${portrait.trends[t].change} 分`}}</p>
              <p class="muted">两个时段各至少 3 次已评分任务才判断变化；未校正试题难度。</p>
            </el-card>
            <el-card shadow="never"><h3>完成质量与知识点</h3><ExpandableList :items="metrics.knowledge_points" label="知识点表现" :reset-key="courseId + ':' + classId + ':' + studentId"><template #default="{items:visibleItems}"><el-table :data="visibleItems" empty-text="暂无知识点作答证据"><el-table-column prop="knowledge_point" label="知识点"/><el-table-column prop="answered" label="已作答题数"/><el-table-column label="正确率"><template #default="{row}">{{percentage(row.accuracy)}}</template></el-table-column><el-table-column label="表现"><template #default="{row}">{{row.accuracy<60?'需巩固':row.accuracy<80?'可继续练习':'掌握较好'}}</template></el-table-column></el-table></template></ExpandableList></el-card>
            <el-card shadow="never"><h3>授权自习表现</h3><p>自习 {{(metrics.study.duration_seconds/60).toFixed(1)}} 分钟 · {{metrics.study.sessions}} 场 · 有效采样覆盖 {{percentage(metrics.study.coverage_percent)}}</p><p class="muted">{{metrics.study.note}}。未观测时长 {{(metrics.study.unobserved_seconds/60).toFixed(1)}} 分钟，不计为低专注；有效采样不足 {{metrics.study.minimum_sample_seconds}} 秒不生成专注参考值。</p></el-card>
            <el-card shadow="never"><h3>任务明细</h3><ExpandableList :items="portrait.tasks" label="任务明细" :reset-key="courseId + ':' + classId + ':' + studentId"><template #default="{items:visibleItems}"><el-table :data="visibleItems" empty-text="暂无正式任务记录">
              <el-table-column prop="title" label="任务" min-width="140"/><el-table-column label="截止" min-width="160"><template #default="{row}">{{dateLabel(row.due_at)}}</template></el-table-column>
              <el-table-column label="首次完整提交位置" min-width="190"><template #default="{row}">{{taskRank(row)}}</template></el-table-column>
              <el-table-column label="成绩" width="100"><template #default="{row}">{{row.score ?? (row.submitted ? '无按时成绩' : '未提交')}}</template></el-table-column>
              <el-table-column label="作答完整" width="95"><template #default="{row}">{{row.answered}}/{{row.question_count}}</template></el-table-column>
              <el-table-column label="补交 / 订正" min-width="140"><template #default="{row}">{{row.submission_count}} 次提交；补交 {{row.late_count}} 次<span v-if="row.late_score!=null">，{{row.late_score}} 分</span></template></el-table-column>
            </el-table></template></ExpandableList></el-card>
            <el-card shadow="never"><div class="evaluation-heading"><h3>教学评价 <small class="muted">自动生成，待教师核对</small></h3><el-button type="primary" :loading="evaluating" @click="evaluate">{{portrait.evaluation ? '重新生成评价' : '生成评价'}}</el-button></div>
              <el-alert v-if="portrait.evaluation?.status==='stale'" title="源数据或授权已变化，旧评价已失效，请重新生成。" type="warning" :closable="false"/>
              <template v-if="portrait.evaluation?.content"><section v-for="(label,key) in evaluationLabels" :key="key"><h4>{{label}}</h4><ExpandableList :items="portrait.evaluation.content[key]" :label="label" :limit="3" :reset-key="courseId + ':' + classId + ':' + studentId"><template #default="{items:visibleItems}"><p v-for="(item,index) in visibleItems" :key="index">{{item.text}} <small class="muted">依据：{{item.evidence_ids.join('、')}}</small></p></template></ExpandableList></section><small class="muted">生成时间：{{dateLabel(portrait.evaluation.created_at)}}</small></template>
              <p v-else class="muted">依据本页证据起草评价，供教师核对；数据不足或服务不可用时不生成评价。</p>
              <el-collapse><el-collapse-item title="查看评价证据与数据口径"><p v-for="note in portrait.limitations" :key="note">{{note}}</p><pre>{{JSON.stringify(portrait.evidence,null,2)}}</pre></el-collapse-item></el-collapse>
            </el-card>
          </section>
        </div>
      </el-tab-pane>
      <el-tab-pane label="发布作业 / 考试" name="tasks">
        <el-card shadow="never"><h2>发布班级任务</h2><p class="muted">仅支持已发布题库中的客观题。发布后固定试题、分值与应完成人员；考试仅能提交一次。</p>
          <el-form label-position="top" class="task-publish-form">
            <el-form-item label="任务名称" required :error="publishAttempted && !title.trim() ? '请输入任务名称' : ''"><el-input v-model="title" placeholder="例如：细胞结构课后练习" maxlength="160" /></el-form-item>
            <el-form-item label="任务类型"><el-select v-model="kind" @change="maxSubmissions=1"><el-option label="作业" value="homework"/><el-option label="考试" value="exam"/></el-select></el-form-item>
            <el-form-item label="最多提交次数"><el-select v-model="maxSubmissions" filterable><el-option v-if="kind==='homework'" label="不限次数" value="unlimited"/><el-option v-for="count in 100" :key="count" :label="`${count} 次`" :value="count"/></el-select></el-form-item>
            <el-form-item label="截止时间" required :error="publishAttempted && !due ? '请选择截止时间' : ''"><el-date-picker v-model="due" type="datetime" placeholder="选择日期与时间" /></el-form-item>
            <el-form-item label="已发布题库" required :error="publishAttempted && !versionId ? '请选择题库' : ''"><el-select v-model="versionId" placeholder="选择题库" @change="selectedItems=[]; points={}"><el-option v-for="s in sources" :key="s.version_id" :value="s.version_id" :label="`${s.folder_name || '题库'} · 版本 ${s.version_number}`"/></el-select></el-form-item>
          </el-form>
          <el-table :key="versionId" :data="sourceItems" empty-text="请选择已发布题库；无题目时请先在习题中心审核并发布客观题" @selection-change="selectedItems=$event"><el-table-column type="selection"/><el-table-column prop="stem_markdown" label="题目"/><el-table-column label="分值" width="210"><template #default="{row}"><el-input-number class="task-points-input" :model-value="points[row.item_id] ?? 1" :min="0.01" :max="1000" :precision="2" @update:model-value="points[row.item_id]=$event ?? 1"/></template></el-table-column></el-table>
          <el-button type="primary" :loading="publishing" :disabled="!selectedItems.length" @click="publish">发布给当前班级（{{selectedItems.length}} 题）</el-button>
        </el-card>
        <el-card shadow="never"><h3>已发布任务</h3><ExpandableList :items="tasks" label="已发布任务" :reset-key="courseId + ':' + classId + ':' + studentId"><template #default="{items:visibleItems}"><el-table :data="visibleItems" empty-text="暂无已发布任务"><el-table-column prop="title" label="名称"/><el-table-column label="类型"><template #default="{row}">{{row.kind==='exam'?'考试':'作业'}}</template></el-table-column><el-table-column prop="expected_count" label="应完成人数"/><el-table-column label="截止时间"><template #default="{row}">{{dateLabel(row.due_at)}}</template></el-table-column></el-table></template></ExpandableList></el-card>
      </el-tab-pane>
    </el-tabs>
  </main>
</template>

<style scoped>
.task-publish-form{display:grid;grid-template-columns:1.2fr 1.2fr 1fr 1fr;gap:16px}.task-publish-form :deep(.el-date-editor){width:100%}.task-publish-form :deep(.el-form-item){min-width:0}@media(max-width:1200px){.task-publish-form{grid-template-columns:1fr 1fr}}
.task-points-input{width:100%}

.portrait-page{display:grid;gap:18px}.filters{display:flex;gap:12px;flex-wrap:wrap}.filters>.el-select,.filters>.el-input{width:220px}.portrait-layout{display:grid;grid-template-columns:240px minmax(0,1fr);gap:20px}.portrait-detail{display:grid;gap:18px;min-width:0}.metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.metric-grid strong{display:block;font-size:26px;margin:10px 0;color:#294b3c}.metric-grid small,.student-row small{display:block;color:#56634d}.student-row{display:block;width:100%;text-align:left;padding:13px;border:0;border-radius:8px;background:transparent;cursor:pointer}.student-row.active{background:#e4e9da;color:#294b3c}.score-chart{width:100%;max-height:250px}.evaluation-heading{display:flex;align-items:center;justify-content:space-between}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}h2 small{font-size:13px;font-weight:normal}.el-tab-pane>.el-card{margin-bottom:18px}@media(max-width:1000px){.portrait-layout{grid-template-columns:1fr}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
