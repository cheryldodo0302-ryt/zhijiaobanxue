<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const status = ref<any>({ status: '等待开始', learning: false, score: 0, focus: 0, study_time: 0 })
const records = ref<any[]>([])
const statistics = ref<any>({ total_sessions: 0, total_study_time: 0, average_score: 0, average_focus: 0, best_score: 0 })
const videoUrl = ref('')
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
    if (!status.value.learning) videoUrl.value = ''
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
    const { data } = await api.post('/student/study-room/start')
    status.value = data
    startPolling()
    if (data.camera_available) {
      try {
        const token = (await api.post('/student/study-room/video-token')).data.token
        videoUrl.value = `/api/v1/student/study-room/video?stream_token=${encodeURIComponent(token)}`
      } catch { videoUrl.value = '' }
    }
    ElMessage.success(data.camera_available ? '自习已开始，正在校准摄像头' : '自习计时已开始')
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
    videoUrl.value = ''
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
onMounted(() => { loadData(); startPolling() })
onUnmounted(() => { if (poller) window.clearInterval(poller) })
</script>

<template>
  <main class="content student-study-room" v-loading="loading">
    <header class="student-header">
      <div class="page-title">
        <span class="eyebrow">STUDENT AI STUDY ROOM</span>
        <h1>AI 自习室</h1>
        <p class="muted">用摄像头姿态、在场状态和专注时长帮助你复盘学习节奏；摄像头不可用时自动保留计时功能。</p>
      </div>
      <div class="student-account">
        <el-button plain @click="$router.push('/student/courses')">返回课程</el-button>
        <span>{{ auth.user?.display_name || auth.user?.username }}</span>
        <el-button @click="logout">退出</el-button>
      </div>
    </header>

    <section class="study-room-hero">
      <div>
        <span class="eyebrow">TODAY'S SESSION</span>
        <h2>{{ isLearning ? '保持自己的节奏，系统会记录本场变化' : '准备好后开始一场专注自习' }}</h2>
        <p v-if="status.warning" class="study-room-warning">{{ status.warning }}</p>
        <p v-else class="muted">开始后请正对摄像头约 3 秒完成姿态校准；系统只在本机保存统计结果。</p>
      </div>
      <div class="study-room-actions">
        <el-button v-if="!isLearning" type="primary" size="large" @click="startStudy">开始自习</el-button>
        <el-button v-else type="danger" size="large" @click="finishStudy">结束并保存</el-button>
      </div>
    </section>

    <section class="study-room-layout">
      <el-card shadow="never" class="study-camera-card">
        <template #header><div class="card-heading"><b>实时状态</b><el-tag :type="isLearning ? 'success' : 'info'">{{ status.status || '等待开始' }}</el-tag></div></template>
        <div v-if="videoUrl" class="study-video-wrap"><img :src="videoUrl" alt="AI 自习室摄像头画面" /></div>
        <div v-else class="study-video-placeholder"><span class="study-video-icon">◉</span><b>{{ status.camera_available ? '等待视频画面' : '摄像头 AI 未启用' }}</b><p class="muted">{{ status.camera_available ? '启动后将显示带状态标注的画面。' : '可继续使用计时；安装可选视觉依赖并允许摄像头后启用行为评分。' }}</p></div>
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
        <p class="muted small">评分由行为表现、专注度、稳定性和有效学习率综合组成。计时模式不会虚构行为评分。</p>
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
