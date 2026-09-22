<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
const courses=ref<any[]>([]), classes=ref<any[]>([]), jobs=ref<any[]>([]), questions=ref<any[]>([]), config=ref<any>(null)
const loading=ref(true), error=ref('')
const students=computed(()=>classes.value.reduce((n,x)=>n+Number(x.member_count||0),0))
const pending=computed(()=>[
  ...jobs.value.filter(x=>x.status==='failed'||x.analysis_status==='failed').map(x=>({...x,key:'failed:'+x.course_id+':'+x.job_id,title:x.original_name||'未命名资料',state:'处理失败',type:'danger',path:'/knowledge',action:'查看资料'})),
  ...jobs.value.filter(x=>x.status!=='failed'&&x.analysis_status==='review_required').map(x=>({...x,key:'review:'+x.course_id+':'+x.job_id,title:x.original_name||'未命名资料',state:'待审核',type:'warning',path:'/knowledge',action:'审核资料'})),
  ...courses.value.flatMap(course=>{const count=questions.value.filter(x=>x.course_id===course.course_id&&x.status==='draft').length;return count?[{key:'questions:'+course.course_id,course_id:course.course_id,course_name:course.course_name,title:count+' 道习题等待审核',state:'待审核',type:'warning',path:'/questions',action:'审核习题'}]:[]})
])
const page=ref(1), visiblePending=computed(()=>pending.value.slice((page.value-1)*8,page.value*8))
async function load(){
 loading.value=true;error.value=''
 try{
  const [c,k,s]=await Promise.all([api.get('/teacher/courses'),api.get('/teacher/classes'),api.get('/system/student-import-config')])
  courses.value=c.data;classes.value=k.data;config.value=s.data
  const details=await Promise.all(courses.value.map(async course=>{
   const [j,q]=await Promise.all([api.get(`/teacher/courses/${course.course_id}/ingestion-jobs`),api.get(`/teacher/courses/${course.course_id}/question-bank`)])
   const scope={course_id:course.course_id,course_name:course.course_name}
   return {jobs:j.data.map((x:any)=>({...x,...scope})),questions:q.data.map((x:any)=>({...x,...scope}))}
  }))
  jobs.value=details.flatMap(x=>x.jobs);questions.value=details.flatMap(x=>x.questions);page.value=1
 }catch(e:any){error.value=typeof e.response?.data?.detail==='string'?e.response.data.detail:'暂时无法加载待办，请重试。'}
 finally{loading.value=false}
}
onMounted(load)
</script>
<template>
 <main class="content dashboard">
  <header class="dashboard-heading"><div><h1>工作台概览</h1><p>先处理需要关注的内容，再继续课程建设。</p></div><el-button :loading="loading" @click="load">刷新</el-button></header>
  <el-alert v-if="config&&!config.configured" title="学生初始密码未配置" description="请联系系统维护人员配置后再导入学生名单。" type="error" :closable="false"/>
  <el-alert v-else-if="config?.security_level==='weak'" title="学生统一初始密码强度较弱" description="建议提升密码强度；学生首次登录仍必须修改密码。" type="warning" :closable="false"/>
  <section class="overview-totals" aria-label="教学规模"><div><span>共享课程</span><strong>{{courses.length}}</strong></div><div><span>教学班</span><strong>{{classes.length}}</strong></div><div><span>班级成员人次</span><strong>{{students}}</strong></div><el-button @click="$router.push('/teaching')">管理课程与教学班</el-button></section>
  <section class="pending-panel" aria-labelledby="pending-heading">
   <div class="pending-heading"><div><h2 id="pending-heading">待处理事项</h2><p>优先显示处理失败的资料，随后是待审核内容。</p></div><span v-if="!loading&&!error">{{pending.length}} 项</span></div>
   <el-skeleton v-if="loading" :rows="5" animated/>
   <el-alert v-else-if="error" :title="error" type="error" :closable="false"><el-button @click="load">重新加载</el-button></el-alert>
   <template v-else>
    <el-empty v-if="!pending.length" :description="courses.length?'当前没有待处理事项':'创建共享课程后，即可上传资料和安排教学任务'" :image-size="90"><el-button v-if="!courses.length" type="primary" @click="$router.push('/teaching')">创建共享课程</el-button></el-empty>
    <ul v-else class="pending-list"><li v-for="(item,index) in visiblePending" :key="item.key" :style="{'--item-order':index}">
      <div class="pending-copy"><strong>{{item.title}}</strong><span>{{item.course_name}}</span></div>
      <el-tag :type="item.type" effect="light">{{item.state}}</el-tag>
      <el-button @click="$router.push({path:item.path,query:{course:item.course_id}})">{{item.action}}</el-button>
    </li></ul>
    <el-pagination v-if="pending.length>8" v-model:current-page="page" :page-size="8" :total="pending.length" layout="prev, pager, next" />
   </template>
  </section>
 </main>
</template>
<style scoped>
.dashboard{max-width:1440px!important;padding:32px 40px;display:grid;gap:24px}.dashboard-heading,.pending-heading{display:flex;align-items:center;justify-content:space-between;gap:24px}.dashboard-heading h1{font-size:28px;font-weight:650;margin:0 0 8px}.dashboard-heading p,.pending-heading p{color:var(--text-secondary);font-size:14px;margin:0;line-height:1.6}.overview-totals{display:flex;align-items:center;gap:48px;padding:24px 0;border-block:1px solid var(--border-subtle)}.overview-totals>div{min-width:100px;display:grid;gap:8px}.overview-totals span{font-size:13px;color:var(--text-secondary)}.overview-totals strong{font-size:30px;font-weight:600;font-variant-numeric:tabular-nums}.overview-totals>.el-button{margin-left:auto}.pending-panel{background:var(--surface);border:1px solid var(--border-subtle);border-radius:var(--radius-panel);padding:26px}.pending-heading{margin-bottom:24px}.pending-heading h2{font-size:19px;margin:0 0 6px}.pending-heading>span{font-size:13px;color:var(--text-secondary)}.pending-list{list-style:none;padding:0;margin:0}.pending-list li{display:flex;align-items:center;gap:24px;padding:20px 0}.pending-list li+li{border-top:1px solid var(--border-subtle)}.pending-copy{display:grid;gap:7px;flex:1;min-width:0}.pending-copy strong{font-size:15px;font-weight:550;overflow-wrap:anywhere}.pending-copy span{font-size:12px;color:var(--text-secondary)}.el-pagination{justify-content:flex-end;margin-top:18px}
@media(prefers-reduced-motion:no-preference){.dashboard-heading,.overview-totals,.pending-panel{animation:dashboard-enter .5s cubic-bezier(.16,1,.3,1) both}.overview-totals{animation-delay:.05s}.pending-panel{animation-delay:.1s}.pending-list li{animation:dashboard-enter .35s cubic-bezier(.16,1,.3,1) both;animation-delay:calc(var(--item-order)*35ms)}}
@keyframes dashboard-enter{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1100px){.dashboard{padding:24px}.overview-totals{gap:24px}.overview-totals>div{min-width:80px}}
</style>
