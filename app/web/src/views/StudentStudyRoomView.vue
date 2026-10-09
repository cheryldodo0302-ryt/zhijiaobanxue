<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { BrowserStudyAnalyzer, type StudyAiResult } from '../studyRoomAi'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const status = ref<any>({ status: '等待开始', learning: false, score: 0, focus: 0, study_time: 0 })
const records = ref<any[]>([])
const todos = ref<any[]>([])
const todoTitle = ref('')
const todoLoading = ref(false), todoSaving = ref(false), todoError = ref(''), showFinishedTodos = ref(false)
const pendingTodos = computed(() => todos.value.filter(item => item.state === 'pending'))
const finishedTodos = computed(() => todos.value.filter(item => item.state !== 'pending'))
const visibleTodos = computed(() => showFinishedTodos.value ? finishedTodos.value : pendingTodos.value)
async function loadTodos() {
  todoLoading.value = true
  try { todos.value = (await api.get('/student/todos')).data; todoError.value = '' }
  catch (error: any) {
    todoError.value = error.response?.status === 404
      ? '待办接口尚未加载。请确认本地 API 已更新并重新启动。'
      : error.response?.data?.detail || (error.request ? '无法连接待办服务，请确认本地 API 已启动。' : '待办加载失败，请稍后重试。')
  }
  finally { todoLoading.value = false }
}
async function addTodo() {
  const title = todoTitle.value.trim()
  if (!title || todoSaving.value) return
  todoSaving.value = true
  try { await api.post('/student/todos', { title }); todoTitle.value = ''; showFinishedTodos.value = false; await loadTodos() }
  catch (error: any) { ElMessage.error(error.response?.data?.detail || '添加待办失败') }
  finally { todoSaving.value = false }
}
async function setTodoCompleted(item: any, completed: boolean) {
  if (todoSaving.value) return
  todoSaving.value = true
  try { await api.patch(`/student/todos/${item.id.slice(9)}`, { completed }); await loadTodos() }
  catch (error: any) { ElMessage.error(error.response?.data?.detail || '更新待办失败') }
  finally { todoSaving.value = false }
}
async function deleteTodo(item: any) {
  if (todoSaving.value) return
  todoSaving.value = true
  try { await api.delete(`/student/todos/${item.id.slice(9)}`); await loadTodos() }
  catch (error: any) { ElMessage.error(error.response?.data?.detail || '删除待办失败') }
  finally { todoSaving.value = false }
}
function openClassTask(item: any) {
  void router.push({ path: '/student/tasks', query: { class_id: item.class_id, task_id: item.task_id } })
}
function refreshTodosOnFocus() { if (document.visibilityState === 'visible') void loadTodos() }
const sharingScopes = ref<any[]>([]), sharingGrants = ref<any[]>([]), sharingClass = ref(''), sharingEnabled = ref(false)
const sharingBusy = ref(false)
const sharingDialogOpen = ref(false)
async function loadSharing() {
  try {
    const [scopes, grants] = await Promise.all([api.get('/student/task-scopes'), api.get('/student/study-room/grants')])
    sharingScopes.value = scopes.data; sharingGrants.value = grants.data
  } catch { ElMessage.error('自习授权加载失败；默认不共享') }
}
async function revokeSharing(id: string) {
  sharingBusy.value = true
  try { await api.delete(`/student/study-room/grants/${id}`); sharingEnabled.value = false; await loadSharing(); ElMessage.success('已撤销授权，相关教师评价已失效') }
  catch { ElMessage.error('撤销失败，请重试') } finally { sharingBusy.value = false }
}
async function saveSharingGrant() {
  const scope = sharingScopes.value.find(s => s.class_id === sharingClass.value)
  if (!scope) return ElMessage.warning('请选择要共享的课程与班级')
  sharingBusy.value = true
  try {
    await api.post('/student/study-room/grants', { course_id: scope.course_id, class_id: scope.class_id })
    sharingEnabled.value = true
    await loadSharing()
    ElMessage.success('自习共享授权已保存')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '保存授权失败，请重试')
  } finally { sharingBusy.value = false }
}
const statistics = ref<any>({ total_sessions: 0, total_study_time: 0, average_score: 0, average_focus: 0, best_score: 0 })
const videoRef = ref<HTMLVideoElement | null>(null)
const cameraReady = ref(false)
const cameraMessage = ref('摄像头画面只在本浏览器显示，仅上传识别后的统计信号。')
const aiLoading = ref(false)
const aiReady = ref(false)
const aiStatus = ref('等待开始')
let browserStream: MediaStream | null = null
let analyzer: BrowserStudyAnalyzer | null = null
let latestAiResult: StudyAiResult | null = null
let latestAiAt = 0
let latestVideoTime = -1
let poller: number | undefined
let telemetryTimer: number | undefined
let telemetryPromise: Promise<void> | null = null

const isLearning = computed(() => Boolean(status.value.learning))
const minutes = computed(() => Math.floor(Number(status.value.study_time || 0) / 60))
const seconds = computed(() => Math.floor(Number(status.value.study_time || 0) % 60).toString().padStart(2, '0'))
const scoreClass = computed(() => {
  const score = Number(status.value.score || 0)
  return score >= 80 ? 'good' : score >= 60 ? 'normal' : 'low'
})

async function loadData() {
  try {
    const [current, history, summary] = await Promise.all([
      api.get('/student/study-room/status'),
      api.get('/student/study-room/records?limit=12'),
      api.get('/student/study-room/statistics'),
    ])
    status.value = current.data
    records.value = history.data
    statistics.value = summary.data
    if (!status.value.learning) stopStudyCamera()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '自习室状态加载失败')
  }
}

async function sendTelemetry(force = false) {
  if (telemetryPromise) {
    if (force) {
      await telemetryPromise
      return sendTelemetry(true)
    }
    return telemetryPromise
  }
  if (!status.value.learning) return
  telemetryPromise = (async () => {
    try {
      const freshCamera = browserStream?.getVideoTracks().some(track => track.readyState === 'live' && track.enabled && !track.muted)
      const payload = latestAiResult && cameraReady.value && freshCamera && Date.now() - latestAiAt <= 3000
        ? {
            face_ok: latestAiResult.face_ok,
            head_ok: latestAiResult.head_ok,
            eye_closed: latestAiResult.eye_closed,
            person_ok: latestAiResult.person_ok,
            hand_near_face: latestAiResult.hand_near_face,
            hand_count: latestAiResult.hand_count,
            hand_confidence: latestAiResult.hand_confidence,
            head_score: latestAiResult.head_score,
            calibrating: latestAiResult.calibrating,
            camera_available: true,
          }
        : { camera_available: false, calibrating: false }
      const { data } = await api.post('/student/study-room/telemetry', payload)
      status.value = data
      if (data.status) aiStatus.value = data.status
    } catch (error: any) {
      if (force) ElMessage.error(error.response?.data?.detail || '自习状态同步失败')
    }
  })()
  try { await telemetryPromise } finally { telemetryPromise = null }
}

function startTelemetry() {
  if (telemetryTimer) window.clearInterval(telemetryTimer)
  telemetryTimer = window.setInterval(() => { void sendTelemetry() }, 1000)
  void sendTelemetry()
}

function stopTelemetry() {
  if (telemetryTimer) window.clearInterval(telemetryTimer)
  telemetryTimer = undefined
}

function stopStudyCamera() {
  analyzer?.stop()
  stopTelemetry()
  browserStream?.getTracks().forEach(track => track.stop())
  browserStream = null
  cameraReady.value = false
  aiReady.value = false
  aiLoading.value = false
  latestAiResult = null
  aiStatus.value = '等待开始'
  if (videoRef.value) videoRef.value.srcObject = null
}

async function startLocalAi() {
  if (!videoRef.value) return
  aiLoading.value = true
  aiReady.value = false
  cameraMessage.value = '正在加载本地识别服务，摄像头画面不会上传服务器。'
  analyzer = analyzer || new BrowserStudyAnalyzer()
  try {
    await analyzer.load()
    analyzer.start(videoRef.value, result => {
      latestAiResult = result
      if (videoRef.value && videoRef.value.currentTime !== latestVideoTime) {
        latestVideoTime = videoRef.value.currentTime
        latestAiAt = Date.now()
      }
      aiStatus.value = result.calibrating ? '校准中' : (status.value.status || '检测中')
      cameraMessage.value = result.calibrating
        ? '请正对摄像头保持 3 秒，系统正在校准你的坐姿。'
        : '正在本机识别；服务器只接收脱敏统计信号。'
    }, () => {
      if (!aiReady.value) return
      aiReady.value = false
      cameraMessage.value = '本地 模型识别暂时不可用，已切换为仅计时。'
      latestAiResult = null
    })
    aiReady.value = true
    cameraMessage.value = '本地识别已就绪，请正对摄像头完成 3 秒姿态校准。'
  } catch {
    aiReady.value = false
    latestAiResult = null
    cameraMessage.value = '本地识别服务加载失败，已切换为仅计时；摄像头画面仍不会上传。'
  } finally {
    aiLoading.value = false
  }
}

function startPolling() {
  if (poller) window.clearInterval(poller)
  poller = window.setInterval(loadData, 2000)
}

async function startStudy() {
  loading.value = true
  try {
    let sharing = {}
    if (sharingEnabled.value) {
      const scope = sharingScopes.value.find(s => s.class_id === sharingClass.value)
      if (!scope) { ElMessage.warning('请选择要共享的课程与班级'); return }
      sharing = { course_id: scope.course_id, class_id: scope.class_id }
      await api.post('/student/study-room/grants', sharing)
      await loadSharing()
    }
    const { data } = await api.post('/student/study-room/start', sharing)
    status.value = data
    latestAiResult = null
    latestAiAt = 0
    latestVideoTime = -1
    try {
      browserStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
      cameraReady.value = true
      cameraMessage.value = '摄像头已开启，正在启动本地识别。'
      await nextTick()
      if (videoRef.value) videoRef.value.srcObject = browserStream
      await startLocalAi()
      startTelemetry()
    } catch (cameraError: any) {
      stopStudyCamera()
      cameraMessage.value = cameraError?.name === 'NotAllowedError'
        ? '未获得摄像头权限，已切换为仅计时；可在浏览器地址栏重新授权。'
        : '浏览器摄像头不可用，已切换为仅计时。'
      startTelemetry()
    }
    startPolling()
    ElMessage.success(cameraReady.value ? '自习已开始，本地 模型识别已开启' : '自习计时已开始')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '无法开始自习')
  } finally { loading.value = false }
}

async function finishStudy() {
  try { await ElMessageBox.confirm('结束后会保存本次学习记录，确定结束吗？', '结束自习', { type: 'warning' }) } catch { return }
  loading.value = true
  try {
    await sendTelemetry(true)
    const { data } = await api.post('/student/study-room/finish')
    status.value = data
    stopStudyCamera()
    await loadData()
    ElMessage.success(`本次自习已保存，综合分 ${Number(data.score || 0).toFixed(1)}`)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '结束自习失败')
  } finally { loading.value = false }
}

async function clearHistory() {
  try { await ElMessageBox.confirm('只会清空你的自习室历史记录，不能恢复。', '清空记录', { type: 'warning' }) } catch { return }
  await api.delete('/student/study-room/records')
  await loadData()
  ElMessage.success('历史记录已清空')
}

onMounted(() => { loadData(); loadSharing(); loadTodos(); window.addEventListener('focus', refreshTodosOnFocus); startPolling() })
watch(() => route.query.sharing, async () => {
  if (route.query.sharing === 'settings') {
    sharingDialogOpen.value = true
    const { sharing: _sharing, ...query } = route.query
    await router.replace({ path: route.path, query })
  }
}, { immediate: true })
onUnmounted(() => {
  window.removeEventListener('focus', refreshTodosOnFocus)
  if (poller) window.clearInterval(poller)
  stopStudyCamera()
  analyzer?.close()
})
</script>

<template>
  <main class="content student-study-room" :aria-busy="loading">
    <header class="student-header">
      <div class="page-title">
        <h1>自习室</h1>
        <p class="muted">本地 模型识别人脸、姿态、眼睛和手部状态；服务器只保存脱敏统计结果。</p>
      </div>
      <div class="student-account">
        <el-button plain @click="$router.push('/student/courses')">返回课程</el-button>
      </div>
    </header>

    <section class="study-room-hero">
      <div>
        <h2>{{ isLearning ? '保持自己的节奏，系统会记录本场变化' : '准备好后开始一场专注自习' }}</h2>
        <p class="study-room-warning">{{ cameraMessage }}</p>
        <p class="study-room-privacy">{{ isLearning && status.sharing_grant_id ? '本场已关联授权课程，只共享新的脱敏汇总。' : '默认仅自己可见，可在姓名菜单中管理共享授权。' }}</p>
      </div>
      <div class="study-room-actions">
        <el-button v-if="!isLearning" type="primary" size="large" :loading="loading" @click="startStudy">开始自习</el-button>
        <el-button v-else type="danger" size="large" :loading="loading" @click="finishStudy">结束并保存</el-button>
      </div>
    </section>

    <section class="study-room-layout">
      <el-card shadow="never" class="study-camera-card">
        <template #header><div class="card-heading"><b>实时状态</b><el-tag :type="isLearning ? 'success' : 'info'">{{ status.status || aiStatus || '等待开始' }}</el-tag></div></template>
        <div v-show="cameraReady" class="study-video-wrap"><video ref="videoRef" autoplay muted playsinline aria-label="本地摄像头预览" /></div>
        <div v-if="!cameraReady" class="study-video-placeholder"><span class="study-video-icon">◉</span><b>{{ aiLoading ? '正在加载本地识别服务' : '本地摄像头未开启' }}</b><p class="muted">开始自习时浏览器会请求权限；拒绝授权会切换为仅计时。</p></div>
        <div class="study-metrics">
          <div><span>实时分</span><strong :class="scoreClass">{{ Number(status.score || 0).toFixed(1) }}</strong></div>
          <div><span>专注度</span><strong>{{ Number(status.focus || 0).toFixed(1) }}%</strong></div>
          <div><span>本场时长</span><strong>{{ minutes }}:{{ seconds }}</strong></div>
        </div>
      </el-card>

      <div class="study-side-column">
      <el-card shadow="never" class="study-summary-card">
        <template #header><b>我的自习概况</b></template>
        <div class="study-summary-grid">
          <div><span>累计场次</span><strong>{{ statistics.total_sessions || 0 }}</strong></div>
          <div><span>累计时长</span><strong>{{ Math.round(Number(statistics.total_study_time || 0) / 60) }} 分钟</strong></div>
          <div><span>平均综合分</span><strong>{{ Number(statistics.average_score || 0).toFixed(1) }}</strong></div>
          <div><span>最高综合分</span><strong>{{ Number(statistics.best_score || 0).toFixed(1) }}</strong></div>
        </div>
        <el-divider />
        <p class="muted small">模型在浏览器本地运行，不上传画面；后端按参考模型计算专注度、实时分和结束综合分。</p>
        <el-button text type="danger" @click="clearHistory" :disabled="!records.length">清空我的记录</el-button>
      </el-card>
      <el-card shadow="never" class="study-todo-card" :aria-busy="todoLoading">
        <template #header><div class="card-heading"><b>学习待办</b><span class="todo-count">{{ pendingTodos.length }} 项待完成</span></div></template>
        <form class="todo-compose" @submit.prevent="addTodo">
          <el-input v-model="todoTitle" maxlength="120" show-word-limit placeholder="写下一件要完成的事" aria-label="新建个人待办" :disabled="todoSaving || Boolean(todoError)" />
          <el-button native-type="submit" type="primary" :loading="todoSaving" :disabled="!todoTitle.trim() || Boolean(todoError)">添加</el-button>
        </form>
        <div class="todo-switch" role="group" aria-label="待办状态">
          <button type="button" :class="{active:!showFinishedTodos}" @click="showFinishedTodos=false">待完成 <span>{{ pendingTodos.length }}</span></button>
          <button type="button" :class="{active:showFinishedTodos}" @click="showFinishedTodos=true">已完成 / 已结束 <span>{{ finishedTodos.length }}</span></button>
        </div>
        <div v-if="todoError" class="todo-error" role="status"><strong>待办暂时无法加载</strong><p>{{ todoError }}</p><el-button size="small" @click="loadTodos">重新加载</el-button></div>
        <div v-else class="todo-list" role="list" aria-label="学习待办列表">
          <p v-if="!todoLoading && !visibleTodos.length" class="todo-empty">{{ showFinishedTodos ? '这里还没有已完成或已结束的事项。' : '暂无待完成事项，添加一件学习计划吧。' }}</p>
          <div v-for="item in visibleTodos" :key="item.id" class="todo-row" role="listitem">
            <el-checkbox v-if="item.source==='personal'" :model-value="item.state==='completed'" :disabled="todoSaving" :aria-label="`${item.state==='completed'?'取消完成':'完成'}：${item.title}`" @change="setTodoCompleted(item, Boolean($event))" />
            <span v-else class="todo-kind" :class="item.kind">{{ item.kind==='exam'?'考试':'作业' }}</span>
            <div class="todo-copy"><strong :class="{done:item.state!=='pending'}">{{ item.title }}</strong><small v-if="item.source==='class_task'">{{ item.course_name }} · 截止 {{ new Date(item.due_at).toLocaleString('zh-CN') }} · {{ item.state==='closed'?'已结束':item.state==='completed'?'已完成':item.submission_count ? '部分提交，待完成' : '待提交' }}</small><small v-else>个人待办</small></div>
            <el-button v-if="item.source==='class_task'" text type="primary" @click="openClassTask(item)">查看</el-button>
            <el-button v-else text type="danger" :disabled="todoSaving" :aria-label="`删除：${item.title}`" @click="deleteTodo(item)">删除</el-button>
          </div>
        </div>
      </el-card>
      </div>
    </section>

    <el-card shadow="never" class="study-history-card">
      <template #header><div class="card-heading"><b>最近自习记录</b><span class="muted small">仅展示当前学生账号的数据</span></div></template>
      <el-empty v-if="!records.length" description="完成一场自习后，这里会显示记录" />
      <el-table v-else :data="records" stripe>
        <el-table-column prop="date" label="日期" width="130" />
        <el-table-column prop="start_time" label="开始" width="110" />
        <el-table-column label="时长" width="100"><template #default="scope">{{ Math.round(Number(scope.row.study_time || 0) / 60) }} 分钟</template></el-table-column>
        <el-table-column label="综合分" width="100"><template #default="scope"><el-tag>{{ Number(scope.row.score || 0).toFixed(1) }}</el-tag></template></el-table-column>
        <el-table-column label="专注度" width="100"><template #default="scope">{{ Number(scope.row.focus || 0).toFixed(1) }}%</template></el-table-column>
        <el-table-column prop="evaluation" label="评价" min-width="260" />
      </el-table>
    </el-card>

    <el-dialog v-model="sharingDialogOpen" title="自习数据共享" width="min(640px, 94vw)" append-to-body class="sharing-dialog">
      <div class="sharing-dialog-body">
        <div class="sharing-dialog-intro">
          <span class="sharing-dialog-kicker">隐私边界</span>
          <h2>只共享你主动关联的新记录</h2>
          <p>默认仅自己可见。授权后，任课教师只能在对应课程和教学班查看自习汇总，不会看到私人问答、个人课程、摄像头画面或逐帧信号。</p>
        </div>
        <el-alert title="撤销授权会使相关旧评价失效，再次授权也不会恢复撤销前的记录。" type="info" :closable="false" />
        <div class="sharing-dialog-section">
          <div class="sharing-section-heading"><b>本次自习</b><span class="muted small">开始前可选择关联范围</span></div>
          <el-checkbox v-model="sharingEnabled" :disabled="isLearning || loading">本次关联课程，并授权任课教师查看汇总</el-checkbox>
          <el-select v-if="sharingEnabled" v-model="sharingClass" class="sharing-scope-select" placeholder="选择课程与教学班" :disabled="isLearning || loading">
            <el-option v-for="scope in sharingScopes" :key="scope.class_id" :value="scope.class_id" :label="`${scope.course_name} · ${scope.class_name}`" />
          </el-select>
        </div>
        <div class="sharing-dialog-section">
          <div class="sharing-section-heading"><b>当前授权</b><span class="muted small">{{ sharingGrants.length ? `${sharingGrants.length} 项有效授权` : '暂无有效授权' }}</span></div>
          <el-empty v-if="!sharingGrants.length" :image-size="64" description="还没有授权的课程与教学班" />
          <div v-else class="sharing-grants-list">
            <div v-for="grant in sharingGrants" :key="grant.grant_id" class="sharing-grant-row">
              <div><strong>{{ sharingScopes.find(scope => scope.class_id === grant.class_id)?.course_name || '历史课程' }}</strong><span>{{ sharingScopes.find(scope => scope.class_id === grant.class_id)?.class_name || '历史教学班' }}</span></div>
              <el-button text type="danger" :loading="sharingBusy" @click="revokeSharing(grant.grant_id)">撤销授权</el-button>
            </div>
          </div>
        </div>
        <p class="sharing-dialog-note">有效采样累计不足 60 秒时不生成专注参考值。未开启摄像头或未采集到有效信号，不会被计算为低专注。</p>
      </div>
      <template #footer>
        <el-button @click="sharingDialogOpen = false">关闭</el-button>
        <el-button v-if="sharingEnabled && sharingClass" type="primary" :loading="sharingBusy" :disabled="isLearning" @click="saveSharingGrant">保存授权</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.student-study-room{--study-ink:#294b3c;--study-muted:#657268;--study-line:#d7e0d5;max-width:1320px;margin:0 auto;padding:34px clamp(16px,4vw,42px) 56px}
.student-study-room>.student-header{display:flex;align-items:center;justify-content:space-between;gap:28px;margin:0 0 18px;padding:24px 28px;border:1px solid var(--study-line);border-radius:16px;background:rgba(252,252,248,.88);box-shadow:0 12px 28px -22px rgba(41,75,60,.48)}
.student-study-room>.student-header .page-title{min-width:0}.student-study-room>.student-header h1{margin:0 0 8px;color:var(--study-ink);font-size:clamp(30px,4vw,42px);letter-spacing:-.035em;line-height:1.12}.student-study-room>.student-header p{max-width:62ch;margin:0;color:var(--study-muted);font-size:14px;line-height:1.8}.student-account{display:flex;align-items:center;gap:10px;flex-shrink:0}.account-menu{gap:9px;max-width:180px}.account-menu :deep(span){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.study-room-hero{margin:0 0 24px!important;padding:30px 32px!important;border-radius:16px!important;background:linear-gradient(135deg,#f1f6f0 0%,#fcfcf8 72%)!important;box-shadow:0 18px 34px -28px rgba(41,75,60,.6)!important}.study-room-hero h2{margin:0 0 9px!important;font-size:clamp(22px,3vw,30px)!important;line-height:1.35!important;letter-spacing:-.02em}.study-room-warning{margin:0!important;color:#8b5b28!important;font-size:14px;line-height:1.75}.study-room-privacy{margin:6px 0 0;color:#657268;font-size:12px;line-height:1.7}.study-room-actions{padding-top:2px}.study-room-actions :deep(.el-button){min-width:118px;min-height:42px}
.study-room-layout{gap:24px!important}.study-camera-card,.study-summary-card,.study-history-card{border-radius:14px!important;overflow:hidden}.study-camera-card :deep(.el-card__header),.study-summary-card :deep(.el-card__header),.study-history-card :deep(.el-card__header){padding:18px 22px;border-bottom-color:#e5ebe3}.study-camera-card :deep(.el-card__body),.study-summary-card :deep(.el-card__body),.study-history-card :deep(.el-card__body){padding:22px}.study-video-placeholder{min-height:300px}.study-metrics>div,.study-summary-grid>div{padding:16px!important;border:1px solid #e3ebe3;background:#f4f8f4!important}.study-history-card{margin-top:24px!important}.study-history-card :deep(.el-table th.el-table__cell){background:#f4f7f1;color:#52665a}.study-history-card :deep(.el-table td.el-table__cell),.study-history-card :deep(.el-table th.el-table__cell){padding:13px 0}
.study-room-layout{align-items:stretch}.study-side-column{min-width:0;min-height:0;contain:size;display:grid;grid-template-rows:auto minmax(0,1fr);gap:24px}.study-todo-card{min-height:0;border-radius:14px!important;display:flex;flex-direction:column}.study-todo-card :deep(.el-card__header){padding:18px 22px;border-bottom-color:#e5ebe3}.study-todo-card :deep(.el-card__body){display:flex;flex:1;flex-direction:column;gap:14px;min-height:0;padding:18px 22px}.todo-count{font-size:12px;color:#657268;font-variant-numeric:tabular-nums}.todo-compose{display:flex;gap:8px}.todo-compose .el-input{min-width:0}.todo-compose .el-button{flex:none}.todo-switch{display:flex;gap:16px;border-bottom:1px solid #e5ebe3}.todo-switch button{border:0;border-bottom:2px solid transparent;background:none;color:#657268;padding:3px 0 9px;font:inherit;font-size:12px;cursor:pointer}.todo-switch button.active{color:#294b3c;border-bottom-color:#294b3c;font-weight:650}.todo-switch span{font-variant-numeric:tabular-nums}.todo-list{min-height:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-color:#b9c9bd transparent;scrollbar-width:thin}.todo-row{display:flex;align-items:center;gap:10px;min-width:0;padding:10px 0}.todo-row+.todo-row{border-top:1px solid #edf0e9}.todo-row .el-button{margin-left:auto;flex:none}.todo-copy{display:grid;gap:4px;min-width:0;flex:1}.todo-copy strong{font-size:13px;font-weight:600;color:#294b3c;line-height:1.45;overflow-wrap:anywhere}.todo-copy strong.done{color:#657268;text-decoration:line-through}.todo-copy small{font-size:11px;color:#657268;line-height:1.4}.todo-kind{display:grid;place-items:center;flex:none;min-width:36px;padding:3px 5px;border-radius:6px;background:#e3ece6;color:#294b3c;font-size:11px;font-weight:700}.todo-kind.exam{background:#f5e9dd;color:#805b37}.todo-empty{margin:auto 0;padding:14px 0;color:#657268;font-size:13px;line-height:1.6}.todo-switch button:focus-visible{outline:2px solid #294b3c;outline-offset:3px}
.study-video-placeholder{aspect-ratio:auto;height:clamp(300px,34vw,510px)}
.study-camera-card{align-self:start}
.todo-compose :deep(.el-input__inner:focus-visible){outline:none}
.todo-compose .el-input:focus-within{outline:2px solid #355d4b;outline-offset:-2px;border-radius:6px}
.todo-error{display:flex;flex:1;min-height:0;overflow-y:auto;flex-direction:column;align-items:flex-start;gap:7px;padding:10px 12px;border:1px solid #e2e8df;border-radius:8px;background:#f7f8f4;color:#4b6255;font-size:12px}
.todo-error p{margin:0;line-height:1.5}
.sharing-dialog-body{display:grid;gap:18px}.sharing-dialog-intro{padding:4px 2px 0}.sharing-dialog-kicker{display:block;margin-bottom:8px;color:#657f67;font-size:11px;letter-spacing:.12em;font-weight:700}.sharing-dialog-intro h2{margin:0 0 8px;color:#294b3c;font-size:22px;line-height:1.35}.sharing-dialog-intro p,.sharing-dialog-note{margin:0;color:#657268;font-size:13px;line-height:1.85}.sharing-dialog-section{display:grid;gap:12px;padding-top:16px;border-top:1px solid #e5ebe3}.sharing-section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.sharing-scope-select{width:100%}.sharing-grants-list{display:grid;gap:8px}.sharing-grant-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 14px;border:1px solid #dce6dc;border-radius:10px;background:#f8fbf7}.sharing-grant-row>div{display:grid;gap:3px;min-width:0}.sharing-grant-row strong{color:#294b3c;font-size:14px}.sharing-grant-row span{color:#657268;font-size:12px}.sharing-dialog-note{padding:12px 14px;border-radius:10px;background:#f4f6ee;color:#68705e;font-size:12px}
@media(prefers-reduced-motion:no-preference){.student-study-room>.student-header,.study-room-hero,.study-room-layout,.study-history-card{animation:study-room-enter .42s cubic-bezier(.16,1,.3,1) both}.study-room-hero{animation-delay:.04s}.study-room-layout{animation-delay:.08s}.study-history-card{animation-delay:.12s}.sharing-grant-row{transition:transform .2s cubic-bezier(.16,1,.3,1),border-color .18s,background-color .18s}.sharing-grant-row:hover{transform:translateY(-2px);border-color:#9dbba8;background:#f4faf4}}
@keyframes study-room-enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1280px){.study-room-layout{grid-template-columns:1fr!important}.study-side-column{contain:none;grid-template-rows:auto auto}.todo-list{max-height:340px}.study-todo-card{min-height:290px}}
@media(max-width:760px){.student-study-room{padding:20px 14px 36px}.student-study-room>.student-header{display:block;padding:20px 18px}.student-account{justify-content:flex-start;margin-top:16px;flex-wrap:wrap}.student-study-room>.student-header h1{font-size:30px}.study-room-hero{padding:24px 20px!important}.study-room-actions{margin-top:18px}.study-room-layout{grid-template-columns:1fr}.study-camera-card :deep(.el-card__body),.study-summary-card :deep(.el-card__body),.study-history-card :deep(.el-card__body),.study-todo-card :deep(.el-card__body){padding:16px}.sharing-grant-row{align-items:flex-start;flex-direction:column;gap:8px}}
@media(prefers-reduced-motion:reduce){.student-study-room>.student-header,.study-room-hero,.study-room-layout,.study-history-card{animation:none}}
</style>
