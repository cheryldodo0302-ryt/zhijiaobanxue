<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowDown, Reading, Setting, Share } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { clearStudentDrafts } from '../student-navigation'
import AiSettingsDialog from './AiSettingsDialog.vue'
const auth = useAuthStore()
const router = useRouter()
const aiSettingsOpen = ref(false)
async function logout() { clearStudentDrafts(auth.user?.user_id || ''); await auth.logout(); location.href = '/login' }
function openSharing() { void router.push({ path: '/student/study-room', query: { sharing: 'settings' } }) }
function onSettingsChanged(settings: unknown) { window.dispatchEvent(new CustomEvent('student-ai-settings-changed', { detail: settings })) }
</script>
<template>
  <header class="student-portal-nav"><RouterLink to="/student/courses" class="brand"><el-icon><Reading/></el-icon><span>智教伴学<small>以知识陪伴成长</small></span></RouterLink><nav aria-label="学生端导航"><RouterLink to="/student/courses">学习空间</RouterLink><RouterLink to="/student/study-room">自习室</RouterLink><RouterLink to="/student/tasks">班级作业 / 考试</RouterLink></nav><el-dropdown trigger="click" placement="bottom-end"><el-button class="account-menu">{{auth.user?.display_name||auth.user?.username}}<el-icon><ArrowDown/></el-icon></el-button><template #dropdown><el-dropdown-menu><el-dropdown-item :icon="Share" @click="openSharing">自习数据共享</el-dropdown-item><el-dropdown-item :icon="Setting" @click="aiSettingsOpen=true">学习服务设置</el-dropdown-item><el-dropdown-item divided @click="logout">退出</el-dropdown-item></el-dropdown-menu></template></el-dropdown></header>
  <AiSettingsDialog v-model="aiSettingsOpen" @changed="onSettingsChanged" />
</template>
<style scoped>
.student-portal-nav{display:flex;align-items:center;gap:48px;min-height:78px;padding:12px clamp(34px,3vw,54px);border-bottom:1px solid #e0e8e3;background:#fcfcf8}.brand{display:flex;align-items:center;gap:12px;flex:none;font-size:22px;font-weight:650;text-decoration:none;color:#193e38}.brand>.el-icon{font-size:32px;color:#294b3c}.brand small{display:block;font-size:10px;font-weight:400;color:#56634d;letter-spacing:.15em;margin-top:3px}nav{display:flex;align-items:center;gap:32px;flex:1}nav a{padding:14px 0;font-size:14px;text-decoration:none;color:#56634d;border-bottom:2px solid transparent;white-space:nowrap}nav a.router-link-active{border-color:#294b3c;color:#294b3c;font-weight:600}.account-menu{max-width:210px;gap:9px}.account-menu :deep(span){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}a:focus-visible{outline:2px solid #294b3c;outline-offset:4px}
@media(max-width:900px){.student-portal-nav{flex-wrap:wrap;gap:12px}.student-portal-nav>.el-dropdown{margin-left:auto}nav{order:3;flex-basis:100%;gap:24px}}
@media(max-width:760px){.student-portal-nav{padding:16px 22px}.brand{font-size:19px}nav{overflow-x:auto}nav a{font-size:13px}}
</style>
