import {createRouter,createWebHistory} from 'vue-router'
import {useAuthStore} from './stores/auth'
import LoginView from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import TeachingView from './views/TeachingView.vue'
import QuestionCenterView from './views/QuestionCenterView.vue'
import TeachingOverviewView from './views/TeachingOverviewView.vue'
import ChangePasswordView from './views/ChangePasswordView.vue'
import StudentCoursesView from './views/StudentCoursesView.vue'
import StudentStudyRoomView from './views/StudentStudyRoomView.vue'
const router=createRouter({history:createWebHistory(),routes:[
  {path:'/login',component:LoginView,meta:{public:true}},
  {path:'/change-password',component:ChangePasswordView},
  {path:'/',component:DashboardView,meta:{role:'teacher'}},
  {path:'/teaching',component:TeachingView,meta:{role:'teacher'}},
  {path:'/knowledge',component:()=>import('./views/KnowledgeCenterV2View.vue'),meta:{role:'teacher'}},
  {path:'/questions',component:QuestionCenterView,meta:{role:'teacher'}},
  {path:'/analytics',component:TeachingOverviewView,meta:{role:'teacher'}},
  {path:'/student/courses',component:StudentCoursesView,meta:{role:'student'}},
  {path:'/student/study-room',component:StudentStudyRoomView,meta:{role:'student'}},
]})
router.beforeEach(async to=>{const auth=useAuthStore();await auth.restore();if(!auth.user&&!to.meta.public)return'/login';if(auth.user?.must_change_password&&to.path!=='/change-password')return'/change-password';if(auth.user&&!auth.user.must_change_password&&to.path==='/change-password')return auth.user.role==='teacher'?'/':'/student/courses';if(to.meta.role&&auth.user?.role!==to.meta.role)return auth.user?.role==='teacher'?'/':'/student/courses';if(to.path==='/login'&&auth.user)return auth.user.must_change_password?'/change-password':auth.user.role==='teacher'?'/':'/student/courses'})
export default router
