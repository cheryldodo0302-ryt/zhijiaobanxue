import { watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { readWorkspace, selectAvailableCourse, writeWorkspace } from './workspace-storage'

export function useTeacherWorkspace(courses:Ref<any[]>, courseId:Ref<string>, allowAll=false) {
  const auth = useAuthStore()
  const route = useRoute()
  watch(courseId, id => {
    if (courses.value.some(course => course.course_id === id)) {
      writeWorkspace(auth.user?.user_id || '', 'teacher-course', id)
    }
  }, { flush:'sync' })
  function restoreCourse() {
    courseId.value = selectAvailableCourse(
      courses.value.map(course => course.course_id), route.query.course, courseId.value,
      readWorkspace(auth.user?.user_id || '', 'teacher-course', ''), allowAll,
    )
  }
  return { restoreCourse }
}
