<script setup lang="ts">
import { useRoute } from 'vue-router';import { useAuthStore } from '../stores/auth'
const route=useRoute(),auth=useAuthStore();async function logout(){await auth.logout();location.href='/login'}
</script>
<template><aside class="teacher-nav"><div class="logo">智教伴学<small>教师工作台</small></div><nav aria-label="教师工作台主导航"><span class="nav-section">概览</span><router-link to="/">待办与概览</router-link><span class="nav-section">课程</span><router-link to="/teaching">课程与教学班</router-link><router-link to="/teaching-archive">教学档案</router-link><span class="nav-section">内容建设</span><router-link to="/knowledge">知识中心</router-link><router-link to="/knowledge-graph">知识图谱</router-link><router-link to="/questions">习题中心</router-link><span class="nav-section">学情与报告</span><router-link to="/analytics">教学诊断</router-link></nav><div class="nav-user"><span>{{auth.user?.display_name||auth.user?.username}}</span><el-button text @click="logout">退出</el-button></div></aside></template>
