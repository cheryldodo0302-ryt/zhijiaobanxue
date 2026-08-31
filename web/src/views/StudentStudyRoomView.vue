<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const status = ref<any>({ status: '等待开始', learning: false, score: 0, focus: 0, study_time: 0 })
const records = ref<any[]>([])
const statistics = ref<any>({ total_sessions: 0, total_study_time: 0, average_score: 0, average_focus: 0, best_score: 0 })
const videoRef = ref<HTMLVideoElement | null>(null)
const cameraReady = ref(false)
const cameraMessage = ref('摄像头画面只在本浏览器显示，不会上传服务器。')
let browserStream: MediaStream | null = null
let poller: number | undefined

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
    if (!status.value.learning) stopBrowserCamera()
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '自习室状态加载失败')
  }
}

function startPolling() {
  if (poller) window.clearInterval(poller)
  poller = window.setInterval(loadData, 2000)
}

async function startStudy() {
  loading.value = true
  try {
    try {
      browserStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
      cameraReady.value = true
      cameraMessage.value = '摄像头画面只在本浏览器显示，不会上传服务器。'
      await nextTick()
      if (videoRef.value) videoRef.value.srcObject = browserStream
    } catch (cameraError: any) {
      stopBrowserCamera()
      cameraMessage.value = cameraError?.name === 'NotAllowedError'
        ? '未获得摄像头权限，已切换为仅计时；可在浏览器地址栏重新授权。'
        : '浏览器摄像头不可用，已切换为仅计时。'
    }
    const { data } = await api.post('/student/study-room/start')
    status.value = data
    startPolling()
    ElMessage.success(cameraReady.value ? '自习已开始，本地摄像头预览已开启' : '自习计时已开始')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '无法开始自习')
  } finally { loading.value = false }
}

async function finishStudy() {
  try { await ElMessageBox.confirm('结束后会保存本次学习记录，确定结束吗？', '结束自习', { type: 'warning' }) } catch { return }
  loading.value = true
  try {
    const { data } = await api.post('/student/study-room/finish')
    status.value = data
    stopBrowserCamera()
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
function stopBrowserCamera() {
  browserStream?.getTracks().forEach(track => track.stop())
  browserStream = null
  cameraReady.value = false
  if (videoRef.value) videoRef.value.srcObject = null
}
onMounted(() => { loadData(); startPolling() })
onUnmounted(() => { if (poller) window.clearInterval(poller); stopBrowserCamera() })
</script>

<template>
  <main class="content student-study-room" :aria-busy="loading">
    <header class="student-header">
      <div class="page-title">
        <h1>AI 自习室</h1>
        <p class="muted">记录专注时长，并在当前浏览器中提供本地摄像头预览；摄像头不可用时仍可计时。</p>
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
        <template #header><div class="card-heading"><b>实时状态</b><el-tag :type="isLearning ? 'success' : 'info'">{{ status.status || '等待开始' }}</el-tag></div></template>
        <div v-show="cameraReady" class="study-video-wrap"><video ref="videoRef" autoplay muted playsinline aria-label="本地摄像头预览" /></div>
        <div v-if="!cameraReady" class="study-video-placeholder"><span class="study-video-icon">◉</span><b>本地摄像头未开启</b><p class="muted">开始自习时浏览器会请求权限；拒绝授权不会影响计时。</p></div>
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
        <p class="muted small">当前仅记录可核验的自习时长，不上传画面，也不根据摄像头内容生成行为评分。</p>
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
