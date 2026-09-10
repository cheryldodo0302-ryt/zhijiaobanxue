import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from './stores/auth'

const router = createRouter({ history:createWebHistory(), routes:[
  { path:'/login', component:() => import('./views/LoginView.vue'), meta:{ public:true } },
  { path:'/change-password', component:() => import('./views/ChangePasswordView.vue') },
  { path:'/', component:() => import('./views/DashboardView.vue'), meta:{ role:'teacher' } },
  { path:'/teaching', component:() => import('./views/TeachingView.vue'), meta:{ role:'teacher' } },
  { path:'/teaching-archive', component:() => import('./views/TeachingArchiveWorkbenchView.vue'), meta:{ role:'teacher' } },
  { path:'/knowledge', component:() => import('./views/KnowledgeCenterV2View.vue'), meta:{ role:'teacher' } },
  { path:'/knowledge-graph', component:() => import('./views/KnowledgeGraphView.vue'), meta:{ role:'teacher' } },
  { path:'/questions', component:() => import('./views/QuestionCenterView.vue'), meta:{ role:'teacher' } },
  { path:'/analytics', component:() => import('./views/TeachingOverviewView.vue'), meta:{ role:'teacher' } },
  { path:'/student/courses', component:() => import('./views/StudentCoursesView.vue'), meta:{ role:'student' } },
  { path:'/student/study-room', component:() => import('./views/StudentStudyRoomView.vue'), meta:{ role:'student' } },
] })

router.beforeEach(async to => {
  const auth = useAuthStore()
  await auth.restore()
  if (!auth.user && !to.meta.public) return { path:'/login', query:{ returnTo:to.fullPath } }
  if (auth.user?.must_change_password && to.path !== '/change-password') return '/change-password'
  if (auth.user && !auth.user.must_change_password && to.path === '/change-password') return auth.user.role === 'teacher' ? '/' : '/student/courses'
  if (to.meta.role && auth.user?.role !== to.meta.role) return auth.user?.role === 'teacher' ? '/' : '/student/courses'
  if (to.path === '/login' && auth.user) return auth.user.must_change_password ? '/change-password' : auth.user.role === 'teacher' ? '/' : '/student/courses'
})

export default router
