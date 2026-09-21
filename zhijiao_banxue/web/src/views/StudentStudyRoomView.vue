<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { BrowserStudyAnalyzer, type StudyAiResult } from '../studyRoomAi'

const auth = useAuthStore()
const loading = ref(false)
const status = ref<any>({ status: '等待开始', learning: false, score: 0, focus: 0, study_time: 0 })
const records = ref<any[]>([])
const sharingScopes = ref<any[]>([]), sharingGrants = ref<any[]>([]), sharingClass = ref(''), sharingEnabled = ref(false)
const sharingBusy = ref(false)
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

async function logout() { await auth.logout(); location.href = '/login' }
onMounted(() => { loadData(); loadSharing(); startPolling() })
onUnmounted(() => {
  if (poller) window.clearInterval(poller)
  stopStudyCamera()
  analyzer?.close()
})
</script>

<template>
  <main class="content student-study-room" :aria-busy="loading">
    <el-card shadow="never">
      <h3>自习数据共享（可选）</h3>
      <p v-if="isLearning">当前会话：{{status.sharing_grant_id && sharingGrants.some(g=>g.grant_id===status.sharing_grant_id) ? '已关联课程并授权共享' : '仅自己可见'}}</p>
      <p class="muted">默认仅自己可见。主动授权后，任课教师可查看关联课程的新自习汇总；不会共享历史私人记录或摄像头画面。</p>
      <el-checkbox v-model="sharingEnabled" :disabled="isLearning || loading">本次关联课程，并授权任课教师查看汇总</el-checkbox>
      <el-select v-if="sharingEnabled" v-model="sharingClass" placeholder="课程与教学班" :disabled="isLearning || loading"><el-option v-for="s in sharingScopes" :key="s.class_id" :value="s.class_id" :label="`${s.course_name} · ${s.class_name}`"/></el-select>
      <p v-for="g in sharingGrants" :key="g.grant_id">已授权：{{sharingScopes.find(s=>s.class_id===g.class_id)?.class_name || '历史教学班'}} <el-button text type="danger" :loading="sharingBusy" @click="revokeSharing(g.grant_id)">撤销授权</el-button></p>
      <small class="muted">撤销后教师不能再查看该授权的数据；重新授权不恢复旧记录。有效采样累计不足 60 秒时不生成专注参考值，未开启摄像头或采样不足不会计为低专注。</small>
    </el-card>
    <header class="student-header">
      <div class="page-title">
        <h1>自习室</h1>
        <p class="muted">本地 模型识别人脸、姿态、眼睛和手部状态；服务器只保存脱敏统计结果。</p>
      </div>
      <div class="student-account">
        <el-button plain @click="$router.push('/student/courses')">返回课程</el-button>
        <span>{{ auth.user?.display_name || auth.user?.username }}</span>
        <el-button @click="logout">退出</el-button>
      </div>
    </header>

    <section class="study-room-hero">
      <div>
        <h2>{{ isLearning ? '保持自己的节奏，系统会记录本场变化' : '准备好后开始一场专注自习' }}</h2>
        <p class="study-room-warning">{{ cameraMessage }}</p>
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
  </main>
</template>
