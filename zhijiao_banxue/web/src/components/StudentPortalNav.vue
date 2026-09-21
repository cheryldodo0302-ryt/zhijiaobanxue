<script setup lang="ts">
import { Reading } from '@element-plus/icons-vue'
</script>
<template>
  <header class="student-portal-nav"><RouterLink to="/student/courses" class="brand"><el-icon><Reading/></el-icon><span>智教伴学<small>以知识陪伴成长</small></span></RouterLink><nav aria-label="学生端导航"><RouterLink to="/student/courses">学习空间</RouterLink><RouterLink to="/student/study-room">自习室</RouterLink><RouterLink to="/student/tasks">班级作业 / 考试</RouterLink></nav></header>
</template>
<style scoped>
.student-portal-nav{display:flex;align-items:center;gap:48px;min-height:78px;padding:12px 30px;border-bottom:1px solid #e0e8e3}.brand{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:650;text-decoration:none;color:#193e38}.brand>.el-icon{font-size:32px;color:#294b3c}.brand small{display:block;font-size:10px;font-weight:400;color:#56634d;letter-spacing:.15em;margin-top:3px}nav{display:flex;gap:32px}nav a{padding:14px 0;font-size:14px;text-decoration:none;color:#56634d;border-bottom:2px solid transparent}nav a.router-link-active{border-color:#294b3c;color:#294b3c;font-weight:600}a:focus-visible{outline:2px solid #294b3c;outline-offset:4px}
@media(max-width:760px){.student-portal-nav{flex-wrap:wrap;padding:16px;gap:10px}.brand{font-size:19px}nav{flex-basis:100%;gap:24px}nav a{font-size:13px}}
</style>
