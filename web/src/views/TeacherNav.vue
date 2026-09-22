<script setup lang="ts">
import { computed } from 'vue'
import StudyArtwork from '../components/StudyArtwork.vue'
import { Reading, House, School, FolderOpened, Collection, Share, Tickets, DataAnalysis, User } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
const auth = useAuthStore()
const displayName = computed(() => auth.user?.display_name || auth.user?.username || '教师')
const groups = [
  { label: '概览', items: [{ to: '/', label: '待办与概览', icon: House }] },
  { label: '课程', items: [{ to: '/teaching', label: '课程与教学班', icon: School }, { to: '/teaching-archive', label: '教学档案', icon: FolderOpened }] },
  { label: '内容建设', items: [{ to: '/knowledge', label: '知识中心', icon: Collection }, { to: '/knowledge-graph', label: '知识图谱', icon: Share }, { to: '/questions', label: '习题中心', icon: Tickets }] },
  { label: '学情与报告', items: [{ to: '/analytics', label: '教学诊断', icon: DataAnalysis }, { to: '/student-portraits', label: '学生画像与任务', icon: User }] },
]
async function logout() { await auth.logout(); location.href = '/login' }
</script>
<template>
  <aside class="teacher-nav">
    <RouterLink to="/" class="teacher-brand" aria-label="智教伴学教师工作台首页"><el-icon aria-hidden="true"><Reading /></el-icon><span>智教伴学<small>教师工作台</small></span></RouterLink>
    <nav aria-label="教师工作台主导航">
      <div v-for="group in groups" :key="group.label" class="teacher-nav-group">
        <span class="nav-section">{{ group.label }}</span>
        <RouterLink v-for="item in group.items" :key="item.to" :to="item.to"><el-icon aria-hidden="true"><component :is="item.icon" /></el-icon><span>{{ item.label }}</span></RouterLink>
      </div>
    </nav>
    <StudyArtwork class="teacher-nav-artwork" />
    <div class="nav-user"><span class="teacher-avatar" aria-hidden="true">{{ displayName.slice(0, 1) }}</span><div class="teacher-account"><span :title="displayName">{{ displayName }}</span><small>教师账号</small></div><el-button text @click="logout">退出</el-button></div>
  </aside>
</template>
