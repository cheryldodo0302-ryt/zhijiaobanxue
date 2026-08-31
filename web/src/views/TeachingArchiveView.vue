<script setup lang="ts">
import {computed,onMounted,reactive,ref} from 'vue'
import {ElMessage} from 'element-plus'
import MarkdownIt from 'markdown-it'
import {api} from '../api'

const courses=ref<any[]>([]),terms=ref<any[]>([]),courseId=ref(''),selectedClassId=ref(''),archive=ref<any>(null),loading=ref(false),classDialog=ref(false),uploading=ref(false),syllabusFile=ref<File|null>(null),uploadClassIds=ref<string[]>([])
const classForm=reactive({term_id:'',class_name:'',class_variant:'',teaching_time_slot:''})
const markdown=new MarkdownIt({html:false,linkify:true,breaks:true})
const categories=computed(()=>{
  const groups=new Map<string,any[]>()
  for(const item of archive.value?.sections||[]){const key=item.category_label||'其他教学信息';groups.set(key,[...(groups.get(key)||[]),item])}
  return [...groups.entries()].map(([label,items])=>({label,items}))
})
const classGroups=computed(()=>{
  const groups=new Map<string,any[]>()
  for(const item of archive.value?.classes||[]){const key=`${item.academic_year||'未设置年度'} · ${item.teaching_period||item.term_name}`;groups.set(key,[...(groups.get(key)||[]),item])}
  return [...groups.entries()].map(([label,items])=>({label,items}))
})
async function load(){if(!courseId.value)return;loading.value=true;try{archive.value=(await api.get(`/teacher/courses/${courseId.value}/teaching-archive`,{params:selectedClassId.value?{class_id:selectedClassId.value}:{}})).data}catch(e:any){ElMessage.error(e.response?.data?.detail||'教学档案加载失败')}finally{loading.value=false}}
async function changeCourse(){selectedClassId.value='';uploadClassIds.value=[];syllabusFile.value=null;await load()}
function chooseSyllabus(event:Event){syllabusFile.value=(event.target as HTMLInputElement).files?.[0]||null}
async function uploadSyllabus(){if(!syllabusFile.value)return ElMessage.warning('请先选择大纲文件');if(!uploadClassIds.value.length)return ElMessage.warning('请选择这份大纲适用的教学班');const form=new FormData();form.append('file',syllabusFile.value);form.append('class_ids',uploadClassIds.value.join(','));form.append('analysis_mode','local');uploading.value=true;try{await api.post(`/teacher/courses/${courseId.value}/teaching-archive/documents`,form,{timeout:0});syllabusFile.value=null;ElMessage.success('大纲已按所选教学班进入本地解析队列');await load()}catch(e:any){ElMessage.error(e.response?.data?.detail||'大纲上传失败')}finally{uploading.value=false}}
async function createClass(){if(!classForm.term_id||!classForm.class_name)return ElMessage.warning('请选择教学年度/时段并填写班级名称');await api.post('/teacher/classes',{...classForm,course_id:courseId.value});Object.assign(classForm,{term_id:'',class_name:'',class_variant:'',teaching_time_slot:''});classDialog.value=false;ElMessage.success('教学班已创建');await load()}
async function init(){[courses.value,terms.value]=(await Promise.all([api.get('/teacher/courses'),api.get('/teacher/terms')])).map(x=>x.data);if(courses.value.length){courseId.value=courses.value[0].course_id;await load()}}
onMounted(init)
</script>

<template>
  <main class="content teaching-archive" v-loading="loading">
    <div class="page-title"><span class="eyebrow">TEACHING ARCHIVE</span><h1>教学档案</h1><p class="muted">保存课程大纲中的教学管理信息，并按年度、时段和自定义班型组织教学班；这些内容不会进入学生知识库。</p></div>
    <el-card shadow="never" class="scope-card"><el-select v-model="courseId" placeholder="选择课程" @change="changeCourse"><el-option v-for="course in courses" :key="course.course_id" :label="course.course_name" :value="course.course_id"/></el-select><el-select v-model="selectedClassId" clearable placeholder="全部教学班的大纲" @change="load"><el-option v-for="item in archive?.classes||[]" :key="item.class_id" :label="`${item.academic_year||''} ${item.teaching_period||item.term_name} · ${item.class_variant||item.class_name}`" :value="item.class_id"/></el-select><el-alert type="info" :closable="false" title="选择教学班后，只查看该班专属大纲和课程通用大纲。"/></el-card>
    <el-card shadow="never" class="upload-card"><template #header><div class="section-heading"><div><b>上传班级大纲</b><p class="muted">先选择适用教学班；多个班共用同一份大纲时可一次多选。</p></div><el-tag type="success">默认本地解析</el-tag></div></template><div class="upload-grid"><el-select v-model="uploadClassIds" multiple filterable placeholder="选择大纲适用的一个或多个教学班"><el-option v-for="item in archive?.classes||[]" :key="item.class_id" :label="`${item.academic_year||''} ${item.teaching_period||item.term_name} · ${item.class_variant||item.class_name}`" :value="item.class_id"/></el-select><input type="file" accept=".pdf,.docx,.pptx,.md,.txt" @change="chooseSyllabus"/><el-button type="primary" :loading="uploading" @click="uploadSyllabus">上传并解析大纲</el-button></div><el-table v-if="archive?.documents?.length" :data="archive.documents" size="small" class="archive-documents"><el-table-column prop="original_name" label="已上传大纲"/><el-table-column label="适用班级"><template #default="s">{{s.row.class_labels.join('、')}}</template></el-table-column><el-table-column prop="status" label="解析状态"/><el-table-column prop="analysis_status" label="分析状态"/></el-table></el-card>
    <el-row :gutter="18">
      <el-col :span="14"><el-card shadow="never"><template #header><b>大纲教学信息</b></template><el-empty v-if="!categories.length" description="当前大纲尚未识别到教学档案信息"/><el-collapse v-else><el-collapse-item v-for="group in categories" :key="group.label" :title="`${group.label}（${group.items.length}）`"><article v-for="item in group.items" :key="item.node_id" class="archive-section"><div class="section-heading"><b>{{item.title}}</b><el-tag size="small">{{item.original_name}}</el-tag></div><p class="muted">{{[item.chapter_title,item.section_title].filter(Boolean).join(' / ')}} · 第 {{item.source_pages?.join('、')||'—'}} 页</p><div class="archive-markdown" v-html="markdown.render(item.markdown||'暂无正文')"/></article></el-collapse-item></el-collapse></el-card></el-col>
      <el-col :span="10"><el-card shadow="never"><template #header><div class="section-heading"><b>历年教学班矩阵</b><el-button type="primary" link @click="classDialog=true">新增教学班</el-button></div></template><el-empty v-if="!classGroups.length" description="尚未创建教学班"/><section v-for="group in classGroups" :key="group.label" class="class-year"><h3>{{group.label}}</h3><div v-for="item in group.items" :key="item.class_id" class="class-item"><div><b>{{item.class_name}}</b><span>{{item.class_variant||'未设置班型/教学版本'}}</span></div><small>{{item.teaching_time_slot||'未设置具体授课时间'}} · {{item.member_count}} 人</small></div></section></el-card></el-col>
    </el-row>
    <el-dialog v-model="classDialog" title="新增教学班" width="min(560px,92vw)"><el-form label-position="top"><el-form-item label="教学年度/时段"><el-select v-model="classForm.term_id"><el-option v-for="term in terms" :key="term.term_id" :label="`${term.academic_year||''} ${term.teaching_period||term.term_name}`" :value="term.term_id"/></el-select></el-form-item><el-form-item label="正式班级名称"><el-input v-model="classForm.class_name" placeholder="临床一班"/></el-form-item><el-form-item label="班型/教学版本（完全自定义）"><el-input v-model="classForm.class_variant" placeholder="A班（本部） / B班（仁济） / 进阶班"/></el-form-item><el-form-item label="具体授课时间段"><el-input v-model="classForm.teaching_time_slot" placeholder="周一 1-2 节 / 第1-8周"/></el-form-item></el-form><template #footer><el-button @click="classDialog=false">取消</el-button><el-button type="primary" @click="createClass">创建</el-button></template></el-dialog>
  </main>
</template>

<style scoped>
.teaching-archive{display:grid;gap:18px}.scope-card :deep(.el-card__body){display:grid;grid-template-columns:minmax(190px,300px) minmax(240px,380px) 1fr;gap:16px;align-items:center}.upload-grid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(220px,1fr) auto;gap:12px;align-items:center}.archive-documents{margin-top:16px}.archive-section{padding:10px 0 18px;border-bottom:1px solid #edf0f5}.archive-section:last-child{border-bottom:0}.section-heading{display:flex;justify-content:space-between;gap:12px;align-items:center}.section-heading p{margin:4px 0 0}.archive-markdown{margin-top:12px;padding:14px;border-radius:10px;background:#f7f9fc;line-height:1.7}.class-year{padding:4px 0 14px}.class-year h3{margin:8px 0;color:#33425a}.class-item{display:grid;gap:6px;padding:12px;margin-bottom:8px;border:1px solid #e3e8f0;border-radius:10px}.class-item div{display:flex;justify-content:space-between;gap:12px}.class-item span,.class-item small{color:#78839b}@media(max-width:900px){.scope-card :deep(.el-card__body),.upload-grid{grid-template-columns:1fr}}
</style>
