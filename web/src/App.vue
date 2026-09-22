<script setup lang="ts">
import TeacherNav from './views/TeacherNav.vue'
import StudentPortalNav from './components/StudentPortalNav.vue'
import { vCampusDepth } from './campus-motion'
</script>
<template>
  <div v-campus-depth :class="{'teacher-surface':$route.meta.role==='teacher', 'student-surface':$route.meta.role==='student'}">
    <TeacherNav v-if="$route.meta.role==='teacher'"/>
    <div :class="{'teacher-route':$route.meta.role==='teacher'}">
      <StudentPortalNav v-if="$route.meta.role==='student' && $route.path !== '/student/courses'"/>
      <router-view v-slot="{ Component, route }">
        <Transition name="campus-page" mode="out-in" appear>
          <component :is="Component" :key="route.path" />
        </Transition>
      </router-view>
    </div>
  </div>
</template>
