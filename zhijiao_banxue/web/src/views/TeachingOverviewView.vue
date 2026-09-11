<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useTeacherWorkspace } from '../teacher-workspace'
import { useCoursePreferences } from '../course-preferences'

const auth = useAuthStore()
const route = useRoute()
const courses = ref<any[]>([])
const classes = ref<any[]>([])
const courseId = ref('')
const classId = ref('')
const overview = ref<any>(null)
const statistics = ref<any>(null)
const loading = ref(false)
const baseError = ref('')
const overviewError = ref('')
const statisticsError = ref('')
const { restoreCourse } = useTeacherWorkspace(courses, courseId)
useCoursePreferences('teaching-diagnostics', courseId, { classId })
let requestVersion = 0
let disposed = false
let scrolledToStatistics = false
const scopeKey = () => JSON.stringify([auth.user?.user_id, courseId.value, classId.value])
const errorMessage = (error: any, fallback: string) =>
  typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : fallback

async function loadCourses() {
  const userId = auth.user?.user_id
  loading.value = true
  baseError.value = ''
  try {
    const result = await api.get('/teacher/courses')
    if (disposed || userId !== auth.user?.user_id) return
    courses.value = result.data
    restoreCourse()
    await courseChanged()
  } catch (error) {
    if (disposed || userId !== auth.user?.user_id) return
    baseError.value = errorMessage(error, '课程加载失败，请重试')
    loading.value = false
  }
}

async function courseChanged() {
  const request = ++requestVersion
  const id = courseId.value
  const userId = auth.user?.user_id
  const current = () => !disposed && request === requestVersion
    && id === courseId.value && userId === auth.user?.user_id
  classes.value = []
  overview.value = null
  statistics.value = null
  baseError.value = ''
  overviewError.value = ''
  statisticsError.value = ''
  loading.value = Boolean(id)
  if (!id) return
  try {
    const result = await api.get('/teacher/classes', { params: { course_id: id } })
    if (!current()) return
    classes.value = result.data
    if (!classes.value.some(entry => entry.class_id === classId.value)) classId.value = ''
    await loadOverview()
  } catch (error) {
    if (current()) baseError.value = errorMessage(error, '教学班加载失败，请重试')
  } finally {
    if (current()) loading.value = false
  }
}

async function loadOverview() {
  const request = ++requestVersion
  const scope = scopeKey()
  const id = courseId.value
  overview.value = null
  statistics.value = null
  overviewError.value = ''
  statisticsError.value = ''
  loading.value = Boolean(id)
  if (!id) return
  const params = classId.value ? { class_id: classId.value } : {}
  const [diagnosis, questions] = await Promise.allSettled([
    api.get(`/teacher/courses/${id}/teaching-overview`, { params }),
    api.get(`/teacher/courses/${id}/question-bank/statistics`, { params }),
  ])
  if (disposed || request !== requestVersion || scope !== scopeKey()) return
  if (diagnosis.status === 'fulfilled') overview.value = diagnosis.value.data
  else overviewError.value = errorMessage(diagnosis.reason, '教学概况加载失败，请点击刷新重试')
  if (questions.status === 'fulfilled') statistics.value = questions.value.data
  else statisticsError.value = errorMessage(questions.reason, '习题学习统计加载失败，请点击刷新重试')
  loading.value = false
  if (!scrolledToStatistics && route.hash === '#question-statistics') {
    await nextTick()
    if (!disposed && request === requestVersion) {
      document.getElementById('question-statistics')?.scrollIntoView({ block: 'start' })
      scrolledToStatistics = true
    }
  }
}

const severity = (value: string) => value === 'high' ? 'danger' : value === 'medium' ? 'warning' : 'info'
const errorRateColor = (value: number) => value >= 60 ? '#ef4444' : value >= 30 ? '#f59e0b' : '#22c55e'
onMounted(loadCourses)
onUnmounted(() => { disposed = true; requestVersion++ })
</script>

<template>
  <main class="content teaching-overview" v-loading="loading">
    <div class="page-title">
      <span class="eyebrow">TEACHING INSIGHTS</span>
      <h1>教学诊断</h1>
      <p class="muted">在同一课程和教学班下，查看教学概况、习题学习统计与需要改进的内容。</p>
    </div>

    <el-card shadow="never" class="overview-filter">
      <div class="diagnostic-filters">
        <label>课程
          <el-select v-model="courseId" placeholder="选择课程" :disabled="!courses.length" @change="courseChanged">
            <el-option v-for="course in courses" :key="course.course_id" :label="course.course_name" :value="course.course_id" />
          </el-select>
        </label>
        <label>教学班
          <el-select v-model="classId" clearable placeholder="全部教学班（课程匿名聚合）"
                     :disabled="!courseId || loading" @change="loadOverview">
            <el-option v-for="entry in classes" :key="entry.class_id"
                       :label="`${entry.class_name} · ${entry.term_name}`" :value="entry.class_id" />
          </el-select>
        </label>
        <el-button :disabled="!courseId || loading" @click="courseChanged">刷新</el-button>
      </div>
    </el-card>
    <el-alert v-if="baseError" type="error" :closable="false" :title="baseError" show-icon>
      <el-button @click="loadCourses">重新加载</el-button>
    </el-alert>
    <el-empty v-else-if="!loading && !courses.length" description="暂无共享课程，请先在课程与教学班中创建课程">
      <el-button type="primary" @click="$router.push('/teaching')">前往课程与教学班</el-button>
    </el-empty>
    <el-alert v-if="overviewError" type="error" :closable="false" :title="overviewError" show-icon />

    <template v-if="overview">
      <el-alert v-if="overview.scope_note" type="info" :closable="false" :title="overview.scope_note" />
      <h2 class="section-title">课程学习概况</h2>
      <section class="metric-grid six">
        <el-card shadow="never"><span>资料</span><b>{{ overview.knowledge.readiness.document_count }}</b></el-card>
        <el-card shadow="never"><span>已批准知识点</span><b>{{ overview.knowledge.readiness.approved_knowledge_points }}</b></el-card>
        <el-card shadow="never"><span>待处理阻塞</span><b>{{ overview.knowledge.readiness.blockers.length }}</b></el-card>
        <el-card shadow="never"><span>活跃学生</span><b>{{ overview.learning.active_students }}</b></el-card>
        <el-card shadow="never"><span>提问</span><b>{{ overview.learning.question_count }}</b></el-card>
        <el-card shadow="never"><span>全部练习次数</span><b>{{ overview.learning.quiz_count }}</b></el-card>
      </section>
      <el-row :gutter="18" class="diagnostic-row">
        <el-col :xs="24" :lg="14">
          <el-card shadow="never">
            <template #header><b>优先处理</b></template>
            <el-empty v-if="!overview.priorities.length" description="暂无需要优先处理的问题" />
            <el-table v-else :data="overview.priorities">
              <el-table-column label="等级" width="90"><template #default="{ row }"><el-tag :type="severity(row.severity)">{{ row.severity }}</el-tag></template></el-table-column>
              <el-table-column prop="title" label="现象" min-width="220" />
              <el-table-column prop="evidence_count" label="证据数" width="90" />
              <el-table-column label="操作" width="100">
                <template #default="{ row }"><el-button link type="primary" @click="$router.push({ path: row.target, query: { course: courseId } })">处理</el-button></template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
        <el-col :xs="24" :lg="10">
          <el-card shadow="never">
            <template #header><b>知识库发布准备度</b></template>
            <el-result :icon="overview.knowledge.readiness.can_publish ? 'success' : 'warning'"
                       :title="overview.knowledge.readiness.can_publish ? '可以发布' : '暂不可发布'"
                       :sub-title="`当前版本 v${overview.knowledge.readiness.publication.version_number}`" />
            <div v-for="entry in overview.knowledge.readiness.blockers" :key="entry.code" class="diagnostic-item">
              {{ entry.message }} <el-tag>{{ entry.count }}</el-tag>
            </div>
          </el-card>
        </el-col>
      </el-row>
      <el-row :gutter="18" class="diagnostic-row">
        <el-col :xs="24" :lg="12">
          <el-card shadow="never">
            <template #header><b>综合薄弱知识点</b></template>
            <el-table :data="overview.learning.weak_points" empty-text="暂无知识点作答记录">
              <el-table-column prop="knowledge_point" label="知识点" />
              <el-table-column prop="answered" label="作答" width="75" />
              <el-table-column label="正确率" width="90"><template #default="{ row }">{{ row.accuracy }}%</template></el-table-column>
            </el-table>
          </el-card>
        </el-col>
        <el-col :xs="24" :lg="12">
          <el-card shadow="never">
            <template #header><b>资料未覆盖问题</b></template>
            <el-table :data="overview.learning.uncovered_questions" empty-text="暂无未覆盖问题">
              <el-table-column prop="question" label="问题" />
              <el-table-column prop="count" label="次数" width="75" />
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <section v-if="courseId && !baseError" id="question-statistics" class="question-statistics" aria-labelledby="question-statistics-title">
      <div class="statistics-heading">
        <div>
          <h2 id="question-statistics-title">习题学习统计</h2>
          <p class="muted">当前发布题库内，每名学生对每道题取最近一次作答；范围与上方课程、教学班一致。</p>
        </div>
        <el-tag v-if="statistics?.version" type="info">题库发布版本 v{{ statistics.version.version_number }}</el-tag>
      </div>
      <el-alert v-if="statisticsError" type="error" :closable="false" :title="statisticsError" show-icon />
      <el-empty v-else-if="statistics && !statistics.version" description="当前课程尚无已发布题库，发布并完成作答后即可查看学习统计" />
      <template v-else-if="statistics">
        <section class="question-metrics">
          <div class="metric"><span>学生人数</span><strong>{{ statistics.summary.students }}</strong></div>
          <div class="metric"><span>已参与学生</span><strong>{{ statistics.summary.answered }}</strong></div>
          <div class="metric"><span>有效作答</span><strong>{{ statistics.summary.attempts }}</strong></div>
          <div class="metric success"><span>总体正确率</span><strong>{{ statistics.summary.accuracy }}%</strong></div>
        </section>
        <el-card shadow="never" class="stats-card">
          <template #header><h3>习题薄弱知识点</h3></template>
          <el-empty v-if="!statistics.weak_points?.length" description="当前题库还没有可分析的知识点作答数据" :image-size="48" />
          <div v-else class="weak-point-grid">
            <div v-for="point in statistics.weak_points" :key="point.point" class="weak-point-stat">
              <div><b>{{ point.point }}</b><span>{{ point.wrong_count }} / {{ point.attempts }} 次作答答错</span></div>
              <el-progress :percentage="point.error_rate" :color="errorRateColor(point.error_rate)" />
            </div>
          </div>
        </el-card>
        <el-card shadow="never" class="stats-card">
          <template #header><h3>高错误率题目排行榜</h3></template>
          <el-table :data="statistics.ranking" stripe empty-text="当前题库暂无题目">
            <el-table-column label="排名" width="72"><template #default="{ row }">{{ row.rank ?? '—' }}</template></el-table-column>
            <el-table-column prop="stem_markdown" label="题目" min-width="300" show-overflow-tooltip />
            <el-table-column prop="attempts" label="作答人数" width="100" />
            <el-table-column prop="wrong_count" label="答错人数" width="100" />
            <el-table-column label="知识点" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ (row.knowledge_points || []).join('、') || '未标注' }}</template>
            </el-table-column>
            <el-table-column label="错误率" width="180">
              <template #default="{ row }">
                <el-progress v-if="row.attempts" :percentage="row.error_rate" :color="errorRateColor(row.error_rate)" />
                <span v-else class="muted">尚未作答</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        <el-alert type="info" :closable="false" title="教师端仅展示匿名汇总；个人答案和错题由学生在自己的学习画像中查看。" />
      </template>
    </section>
  </main>
</template>

<style scoped>
.diagnostic-filters { display: flex; align-items: end; gap: 14px; flex-wrap: wrap; }
.diagnostic-filters label { display: grid; gap: 7px; flex: 1; min-width: 220px; color: #47655e; font-size: 13px; }
.diagnostic-filters .el-select { width: 100%; }
.diagnostic-row { row-gap: 18px; }
.section-title, .statistics-heading h2 { margin: 22px 0 12px; font-size: 20px; }
.question-statistics { display: grid; gap: 18px; margin-top: 28px; scroll-margin-top: 20px; }
.statistics-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.statistics-heading h2 { margin: 0 0 8px; }
.statistics-heading p, .stats-card h3 { margin: 0; }
.stats-card { border-radius: 16px; }
.stats-card h3 { font-size: 16px; }
.question-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.metric { padding: 18px; background: #f2faf8; border: 1px solid #dce9e5; border-radius: 14px; }
.metric span { display: block; color: #687d77; font-size: 13px; }
.metric strong { display: block; margin-top: 8px; font-size: 30px; color: #173e49; }
.metric.success strong { color: #23746f; }
.weak-point-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.weak-point-stat { padding: 12px 14px; border: 1px solid #e3ece8; border-radius: 12px; background: linear-gradient(135deg, #fff, #f7fbfa); }
.weak-point-stat > div { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; color: #294a42; flex-wrap: wrap; }
.weak-point-stat span { color: #687d77; font-size: 12px; }
.weak-point-stat :deep(.el-progress-bar__outer) { background: #edf3f1; }
@media (max-width: 900px) { .question-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) {
  .weak-point-grid { grid-template-columns: 1fr; }
  .diagnostic-filters label { flex-basis: 100%; min-width: 0; }
}
</style>
