<script setup lang="ts">
import TeacherNav from './views/TeacherNav.vue'
</script>
<template><TeacherNav v-if="$route.meta.role==='teacher'"/><div :class="{'teacher-route':$route.meta.role==='teacher'}"><router-view/></div></template>
