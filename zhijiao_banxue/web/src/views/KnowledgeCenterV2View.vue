<script setup lang="ts">
import { ArrowDown } from '@element-plus/icons-vue'
import {computed,nextTick,onMounted,onUnmounted,ref,watch} from 'vue'
import {api} from '../api'
import {useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {useSingleFileUpload} from '../single-file-upload'
import {clampPage} from '../review-utils'
import {createKnowledgeMarkdown} from '../knowledge-markdown'
import 'katex/dist/katex.min.css'
import{buildCompactKnowledgeTree,normalizeTreeTitle,visibleBranchIdentity}from'../knowledge-tree'
import {useTeacherWorkspace} from '../teacher-workspace'
import {useCoursePreferences} from '../course-preferences'

const courses=ref<any[]>([]),jobs=ref<any[]>([]),trash=ref<any[]>([]),courseId=ref(''),selectedDoc=ref<any>(null),analysis=ref<any>(null),readiness=ref<any>(null)
const {restoreCourse}=useTeacherWorkspace(courses,courseId)
const outlineMode=ref<'document'|'course'>('document'),nodes=ref<any[]>([]),relations=ref<any[]>([]),partitions=ref<any[]>([]),teachingLevels=ref<any[]>([]),teachingScopeFilter=ref(''),selectedMaterialType=ref(''),selectedNode=ref<any>(null),treeRef=ref<any>(null)
const rememberedTeachingScopeIds=ref<string[]>([])
const lastTreeMove=ref<{scopeKey:string,placements:{node_id:string,parent_id:string|null,sort_order:number}[],createdNodeIds:string[]}|null>(null),undoingTreeMove=ref(false)
const previewUrl=ref(''),downloadUrl=ref(''),previewKind=ref('unavailable'),previewError=ref(''),previewText=ref(''),parserStatus=ref<any>(null)
const page=ref(1),pageInput=ref(1),folderFiles=ref<File[]>([]),uploading=ref(false),pptxHost=ref<HTMLElement|null>(null),selectedJobs=ref<any[]>([]),selectedTrash=ref<any[]>([])
const {files:uploadFiles,file,replace:replaceUploadFile,clear:clearUploadFile}=useSingleFileUpload()
const folderInput=ref<HTMLInputElement|null>(null)
const uploadDialogVisible=ref(false),progressDialogVisible=ref(false),trashDialogVisible=ref(false)
const completingReview=ref(false)
const visibilitySaving=ref<string[]>([])
const jobsTable=ref<any>(null)
async function setStudentFileVisible(job:any,visible:boolean){
  const id=job.document_id
  if(visibilitySaving.value.includes(id))return
  visibilitySaving.value.push(id)
  try{
    await api.patch(`/teacher/documents/${id}/student-visibility`,{visible})
    job.student_file_visible=visible?1:0
    ElMessage.success(visible?'已允许学生查看原文件；资料随知识发布后生效':'已关闭学生原文件预览')
  }catch(e){fail(e,'原文件查看权限更新失败')}
  finally{visibilitySaving.value=visibilitySaving.value.filter(value=>value!==id)}
}
const router=useRouter()
const workflowDocuments=computed(()=>new Map((readiness.value?.documents||[]).map((row:any)=>[row.document_id,row])))
const documentWorkflow=(job:any):any=>workflowDocuments.value.get(job.document_id)||{}
const libraryLabel=(job:any)=>({approved:'已批准到知识库',partial:'部分已入库',pending:'待审查 / 批准'} as Record<string,string>)[documentWorkflow(job).library_status]||'待处理'
const libraryDocumentCount=computed(()=>(readiness.value?.documents||[]).filter((row:any)=>['approved','partial'].includes(row.library_status)).length)
const pendingUploadNames=ref<string[]>([])
const pendingReviewIds=ref<string[]>([]),pendingStudentPublishId=ref('')
useCoursePreferences('knowledge-preparation',courseId,{pendingUploadNames,pendingReviewIds,pendingStudentPublishId,teachingScopeFilter,selectedMaterialType,rememberedTeachingScopeIds})
const pptBuffer=ref<ArrayBuffer|null>(null)
let timer:number|undefined,parserTimer:number|undefined,pptResizeTimer:number|undefined,pptResizeObserver:ResizeObserver|undefined,pptRenderedWidth=0,pptRendering=false,pptRenderPending=false
const apiSource=ref<'server'|'custom'|'local'>('server'),aiProvider=ref('openai_compatible'),aiBaseUrl=ref(''),aiModel=ref(''),aiApiKey=ref('')
const aiSettings=ref<any>({has_api_key:false,verification_status:'untested',verification_message:'尚未保存'}),savingAi=ref(false),testingAi=ref(false)
const analysisMode=computed<'api'|'local'>(()=>apiSource.value==='local'?'local':'api')
const useOwnApi=computed(()=>apiSource.value==='custom')
const candidates=ref<any[]>([]),structure=ref<any>(null),reviewWorkspace=ref<HTMLElement|null>(null)
const reviewMode=ref<'candidates'|'outline'>('candidates'),candidateFilter=ref<'pending'|'approved'|'rejected'|'all'>('pending')
const selectedCandidateId=ref(''),candidateContentMode=ref<'preview'|'source'>('preview'),candidateDraftId=ref(''),candidateDraft=ref('')
const fail=(e:any,m:string)=>ElMessage.error(e.response?.data?.detail||m)
const totalPages=computed(()=>Math.max(1,Number(selectedDoc.value?.total_pages||1)))
const pdfUrl=computed(()=>previewUrl.value?`${previewUrl.value}#page=${page.value}&view=FitH`:'')
const visibleNodes=computed(()=>outlineMode.value==='course'&&selectedMaterialType.value?nodes.value.filter(x=>x.material_type===selectedMaterialType.value):nodes.value)
const treeData=computed(()=>buildCompactKnowledgeTree(visibleNodes.value))
const treeScopeKey=computed(()=>`${courseId.value}:${outlineMode.value}:${outlineMode.value==='document'?(selectedDoc.value?.document_id||''):selectedMaterialType.value}`)
const canUndoTreeMove=computed(()=>Boolean(lastTreeMove.value&&lastTreeMove.value.scopeKey===treeScopeKey.value))
const sections=computed(()=>visibleNodes.value.filter(x=>x.node_type==='section'))
const markdown=createKnowledgeMarkdown()
const markdownHtml=computed(()=>markdown.render(previewText.value||''))
const selectedMarkdownHtml=computed(()=>markdown.render(selectedNode.value?.markdown||''))
const selectedNodeClassIds=computed<string[]>({get:()=>selectedNode.value?.class_ids||[],set:value=>{if(selectedNode.value)selectedNode.value.class_ids=value}})
const apiSourceLabel=computed(()=>({server:'服务器默认服务',custom:'教师自有接口',local:'仅本地规则'}[apiSource.value]))
const hasSavedAiKey=computed(()=>Boolean(aiSettings.value?.has_api_key))
const ownAiReady=computed(()=>Boolean(aiBaseUrl.value&&aiModel.value&&(aiProvider.value==='ollama'||aiApiKey.value||hasSavedAiKey.value)))
const aiVerificationType=computed(()=>({connected:'success',failed:'danger',untested:'info'} as Record<string,string>)[String(aiSettings.value?.verification_status||'untested')]||'info')
const aiVerificationLabel=computed(()=>({connected:'连接成功',failed:'连接失败',untested:'未测试'} as Record<string,string>)[String(aiSettings.value?.verification_status||'untested')]||'未测试')
const parserConnected=computed(()=>['ok','healthy'].includes(String(parserStatus.value?.mineru?.status||'').toLowerCase())&&['ok','healthy'].includes(String(parserStatus.value?.pix2text?.status||'').toLowerCase()))
const parserStatusLabel=computed(()=>parserConnected.value?'远程解析已连接':'远程解析未连接')
const parserStatusDetail=computed(()=>`MinerU ${parserStatus.value?.mineru?.status||'unknown'} · Pix2Text ${parserStatus.value?.pix2text?.status||'unknown'} · 知识树 ${parserStatus.value?.knowledge_extractor?.backend||'unknown'}`)
const selectedCandidate=computed(()=>candidates.value.find(x=>x.candidate_id===selectedCandidateId.value)||filteredCandidates.value[0]||candidates.value[0]||null)
const filteredCandidates=computed(()=>candidates.value.filter(candidate=>{
  if(candidateFilter.value==='all')return true
  if(candidateFilter.value==='pending')return !['APPROVED','REJECTED'].includes(candidate.review_status)
  return candidate.review_status===candidateFilter.value.toUpperCase()
}))
const pendingCandidateCount=computed(()=>candidates.value.filter(x=>!['APPROVED','REJECTED'].includes(x.review_status)).length)
const approvedCandidateCount=computed(()=>candidates.value.filter(x=>x.review_status==='APPROVED').length)
const candidateSourceMarkdown=computed(()=>selectedCandidate.value?.source_markdown||selectedCandidate.value?.markdown_content||'')
const candidateSectionMarkdown=computed(()=>selectedCandidate.value?.section_markdown||selectedCandidate.value?.tree_markdown||candidateSourceMarkdown.value)
const candidateOriginalMarkdown=(candidate:any)=>String(candidate?.markdown_content||candidate?.section_markdown||candidate?.tree_markdown||candidate?.source_markdown||'')
const candidateCurrentMarkdown=(candidate:any)=>String(candidate?.teacher_revision||'').trim()?String(candidate.teacher_revision):candidateOriginalMarkdown(candidate)
const candidatePreviewMarkdown=computed(()=>selectedCandidate.value&&candidateDraftId.value===selectedCandidate.value.candidate_id?candidateDraft.value:selectedCandidate.value?.teacher_revision?.trim()||candidateSectionMarkdown.value)
const candidatePreviewHtml=computed(()=>markdown.render(candidatePreviewMarkdown.value||'暂无可预览的原文'))
const knowledgeTypes=[
  ['definition','定义'],['concept','概念'],['principle','原理'],['theorem','定理'],['property','性质'],
  ['formula','公式'],['rule','规则'],['method','方法'],['procedure','流程'],['classification','分类'],
  ['comparison','比较'],['table','表格'],['fact','事实']
]
const knowledgeTypeLabel=(value:string)=>knowledgeTypes.find(x=>x[0]===value)?.[1]||value||'待判断'
const reviewStatusLabel=(value:string)=>({PENDING:'待审核',NEEDS_REVIEW:'需复核',MODIFIED:'教师已修改',APPROVED:'已入库',REJECTED:'已驳回'}[value]||value||'待审核')
const reviewStatusType=(value:string)=>({PENDING:'warning',NEEDS_REVIEW:'warning',MODIFIED:'warning',APPROVED:'success',REJECTED:'danger'}[value]||'info')
const knowledgeNodeStatusLabel=(value:string)=>({draft:'待审核',pending:'待审核',review_required:'待审核',approved:'已入库',published:'已发布',active:'已启用',rejected:'已驳回',withdrawn:'已撤回'} as Record<string,string>)[String(value||'').toLowerCase()]||value||'待处理'
const knowledgeNodeTypeLabel=(value:string)=>({chapter:'章',section:'节',knowledge_point:'知识点'} as Record<string,string>)[String(value||'').toLowerCase()]||value||'知识节点'
const relationTypeLabel=(value:string)=>({part_of:'整体—部分',prerequisite:'前置关系',progression:'后续进阶',parallel:'并列关系',related:'相关关系'} as Record<string,string>)[String(value||'').toLowerCase()]||value||'关联关系'
const candidateChapterPath=(candidate:any)=>Array.isArray(candidate?.chapter_path)&&candidate.chapter_path.length?candidate.chapter_path.join(' / '):'未分配章节'
const teachingLevelLabel=(item:any)=>`${item.academic_year||''} ${item.teaching_period||item.term_name||''} · ${item.class_variant||item.class_name}`.trim()
const rememberedTeachingScopeLabel=computed(()=>rememberedTeachingScopeIds.value.map(id=>teachingLevels.value.find(item=>item.class_id===id)).filter(Boolean).map(teachingLevelLabel).join('、'))
const selectedNodeIsCourseWide=computed(()=>Boolean(selectedNode.value&&selectedNode.value.node_type==='knowledge_point'&&!selectedNode.value.class_ids?.length))
const materialTypes=[
  ['syllabus','教学大纲'],['lesson_plan','教案'],['slides','课件'],
  ['textbook','教材'],['experiment','实验资料'],['question_bank','题库'],
  ['knowledge_graph','知识图谱'],['teaching_schedule','教学进度'],['other','其他']
]
const materialLabel=(value:string)=>materialTypes.find(x=>x[0]===value)?.[1]||'其他'
const selectedBreadcrumb=computed(()=>{if(!selectedNode.value)return'';const map=new Map(nodes.value.map(x=>[x.node_id,x]));const values:string[]=[];let current:any=selectedNode.value;while(current){values.unshift(current.title);current=current.parent_id?map.get(current.parent_id):null}if(outlineMode.value==='course')values.unshift(materialLabel(selectedNode.value.material_type));return values.filter((x,i)=>i===0||normalizeTreeTitle(x)!==normalizeTreeTitle(values[i-1])).join(' / ')})
const parsePercent=(job:any)=>Math.round(Math.max(0,Math.min(100,Number(job.progress||0))))
const analysisPercent=(job:any)=>{
  if(['review_required','completed'].includes(job.analysis_status))return 100
  const total=Number(job.analysis_total_batches||0),current=Number(job.analysis_current_batch||0)
  return total>0?Math.max(0,Math.min(99,Math.round(current*100/total))):0
}
const overallPercent=(job:any)=>Math.round((parsePercent(job)+analysisPercent(job))/2)
const currentStage=(job:any)=>job.knowledge_review_status==='approved'?'整本已批准到知识库':parsePercent(job)<100?'文档解析':job.analysis_status==='review_required'?'等待教师审核':job.analysis_status==='completed'?'处理完成':job.analysis_mode==='local'?'本地知识整理':'API 知识分析'
const documentStatusLabel=(job:any)=>{
  const status=String(job?.status||'')
  if(status==='queued')return'排队中'
  if(status==='running')return`解析中 ${parsePercent(job)}%`
  if(status==='review_required')return'解析完成，待审核'
  if(['ready','completed'].includes(status))return'解析完成'
  if(status==='failed')return'解析失败'
  if(status==='retry_wait')return'等待重试'
  if(status==='cancelled')return'已取消'
  return status?'处理中':'未开始'
}
const documentStatusType=(job:any)=>({failed:'danger',review_required:'warning',running:'primary',queued:'info',retry_wait:'warning',cancelled:'info',ready:'success',completed:'success'} as Record<string,string>)[String(job?.status||'')]||'info'
const analysisStatusLabel=(job:any)=>{
  const status=String(job?.analysis_status||job?.status||'')
  if(!status)return'未开始'
  if(status==='queued')return'等待分析'
  if(status==='running')return`分析中 ${analysisPercent(job)}%`
  if(status==='retry_wait')return'等待重试'
  if(status==='review_required')return'待教师审核'
  if(status==='completed')return'分析完成'
  if(status==='failed')return'分析失败'
  if(status==='cancelled')return'已取消'
  return status
}
const analysisStatusType=(job:any)=>({failed:'danger',review_required:'warning',running:'primary',queued:'info',retry_wait:'warning',cancelled:'info',completed:'success'} as Record<string,string>)[String(job?.analysis_status||'')]||'info'
const analysisStageLabel=(value:any)=>({queued:'排队中',running:'分析中',waiting_for_service:'等待分析服务',local_content_filter:'本地内容整理',local_course_outline:'本地课程结构整理',classification_and_chunking:'内容分类与知识点整理',course_outline:'课程结构整理',course_reduce:'课程知识归并',document_reduce:'文档知识归并',ppt_title_hierarchy:'PPT 标题层级整理',docling_graph_extraction:'文档结构提取',teacher_review:'教师审核',completed:'已完成',failed:'分析失败',cancelled:'已取消'} as Record<string,string>)[String(value||'')]||value||'处理中'

async function loadAiSettings(){const result=(await api.get('/teacher/ai-settings')).data;aiSettings.value=result;aiProvider.value=result.provider||'openai_compatible';aiBaseUrl.value=result.base_url||'';aiModel.value=result.model||''}
async function refreshParserStatus(){try{parserStatus.value=(await api.get('/system/parser-status')).data}catch{const previous=parserStatus.value||{};parserStatus.value={...previous,mineru:{...(previous.mineru||{}),status:'unreachable'},pix2text:{...(previous.pix2text||{}),status:'unreachable'}}}}
function onParserVisibilityChange(){if(document.visibilityState==='visible')void refreshParserStatus()}
function rememberTeachingScope(ids:string[]){if(!courseId.value)return;const allowed=new Set(teachingLevels.value.map(item=>String(item.class_id)));rememberedTeachingScopeIds.value=[...new Set(ids.map(String))].filter(id=>allowed.has(id))}
function applyRememberedTeachingScope(){if(!selectedNode.value||selectedNode.value.node_type!=='knowledge_point'||!rememberedTeachingScopeIds.value.length)return;selectedNode.value.class_ids=[...rememberedTeachingScopeIds.value];ElMessage.info('已沿用上次教学层级，请点击“保存教学层级”确认')}
async function load(){const[c,p]=await Promise.all([api.get('/teacher/courses'),api.get('/system/parser-status')]);courses.value=c.data;parserStatus.value=p.data;restoreCourse();await Promise.all([loadJobs(),loadAiSettings()])}
async function loadJobs(){if(!courseId.value)return;const id=courseId.value;const[j,r,t]=await Promise.all([api.get(`/teacher/courses/${courseId.value}/ingestion-jobs`),api.get(`/teacher/courses/${courseId.value}/knowledge-workflow`),api.get(`/teacher/courses/${courseId.value}/knowledge-trash`)]);if(courseId.value!==id)return;const selectedIds=new Set(selectedJobs.value.map((job:any)=>job.document_id));jobs.value=j.data.map((fresh:any)=>{const old=jobs.value.find((job:any)=>job.document_id===fresh.document_id);if(!old)return fresh;const edits=selectedIds.has(fresh.document_id)?{material_type:old.material_type,tags:old.tags}:{};Object.assign(old,fresh,edits);return old});selectedJobs.value=selectedJobs.value.filter((job:any)=>jobs.value.some((fresh:any)=>fresh.document_id===job.document_id));for(const selected of jobsTable.value?.getSelectionRows()||[]){if(!jobs.value.some((job:any)=>job.document_id===selected.document_id))jobsTable.value?.toggleRowSelection(selected,false)}readiness.value=r.data;if(!selectedTrash.value.length)trash.value=t.data;if(selectedDoc.value)selectedDoc.value=(selectedJobs.value.length?j.data:jobs.value).find((x:any)=>x.document_id===selectedDoc.value.document_id)||selectedDoc.value}
async function changeCourse(){readiness.value=null;selectedJobs.value=[];selectedTrash.value=[];selectedDoc.value=null;selectedNode.value=null;selectedCandidateId.value='';candidateDraftId.value='';candidateDraft.value='';analysis.value=null;nodes.value=[];relations.value=[];partitions.value=[];teachingLevels.value=[];trash.value=[];candidates.value=[];previewUrl.value='';downloadUrl.value='';await loadJobs()}
function resetUploadSelection(){clearUploadFile();folderFiles.value=[];if(folderInput.value)folderInput.value.value=''}
watch(uploadDialogVisible,visible=>{if(visible)resetUploadSelection()})
function uploadIdentity(item:File){return ((item as any).webkitRelativePath||item.name)+':'+item.size+':'+item.lastModified}
function chooseFolder(event:Event){
  const input=event.target as HTMLInputElement,supported=['.pdf','.docx','.pptx','.md','.markdown','.txt']
  const all=Array.from(input.files||[]).filter(item=>supported.some(ext=>item.name.toLowerCase().endsWith(ext)))
  const pending=new Set(pendingUploadNames.value)
  const matching=all.filter(item=>pending.has(uploadIdentity(item)))
  folderFiles.value=matching.length?matching:all
  if(matching.length)ElMessage.info('已恢复上次未完成的文件，成功文件不会重复上传')
  if(!folderFiles.value.length)ElMessage.warning('所选目录中没有支持的课程资料')
}
async function uploadFolder(){
  if(!folderFiles.value.length||uploading.value)return
  const id=courseId.value,remaining=[...folderFiles.value]
  pendingUploadNames.value=remaining.map(uploadIdentity)
  uploading.value=true
  let accepted=0
  const failed:File[]=[]
  try{
    for(const item of remaining){
      const form=new FormData();form.append('file',item);form.append('relative_path',(item as any).webkitRelativePath||item.name);form.append('analysis_mode','local')
      try{
        await api.post(`/teacher/courses/${id}/documents`,form,{timeout:0})
        accepted++
        if(courseId.value===id)pendingUploadNames.value=pendingUploadNames.value.filter(x=>x!==uploadIdentity(item))
      }catch{failed.push(item)}
    }
    if(courseId.value!==id)return
    folderFiles.value=failed
    if(failed.length)ElMessage.warning(`已提交 ${accepted} 个，剩余 ${failed.length} 个已保留；再次点击只重试失败项。刷新后可重选同一目录继续。`)
    else ElMessage.success(`整包 ${accepted} 个文件已进入解析队列`)
    await loadJobs();uploadDialogVisible.value=failed.length>0;progressDialogVisible.value=true
  }finally{uploading.value=false}
}
async function upload(){if(!file.value||uploading.value)return;if(analysisMode.value==='api'&&useOwnApi.value&&!ownAiReady.value)return ElMessage.warning(aiProvider.value==='ollama'?'请填写 Ollama Base URL 和模型':'请完整填写并保存自有 API 配置');const form=new FormData();form.append('file',file.value);form.append('analysis_mode',analysisMode.value);if(analysisMode.value==='api'&&useOwnApi.value){form.append('ai_provider',aiProvider.value);form.append('ai_base_url',aiBaseUrl.value);form.append('ai_model',aiModel.value);form.append('ai_api_key',aiApiKey.value);form.append('use_saved_ai',String(!aiApiKey.value&&hasSavedAiKey.value))}uploading.value=true;try{await api.post(`/teacher/courses/${courseId.value}/documents`,form,{timeout:0});clearUploadFile();aiApiKey.value='';ElMessage.success(analysisMode.value==='api'?'资料已进入解析与 API 语义分析队列':'资料已进入解析与仅本地分析队列');await loadJobs();uploadDialogVisible.value=false;progressDialogVisible.value=true}catch(e){fail(e,'上传失败')}finally{uploading.value=false}}
function stopPptObserver(){pptResizeObserver?.disconnect();pptResizeObserver=undefined;if(pptResizeTimer)window.clearTimeout(pptResizeTimer);pptResizeTimer=undefined}
function schedulePptRender(){if(pptResizeTimer)window.clearTimeout(pptResizeTimer);pptResizeTimer=window.setTimeout(()=>void renderPptPreview(),180)}
function observePptHost(){stopPptObserver();if(!pptxHost.value||previewKind.value!=='pptx')return;pptResizeObserver=new ResizeObserver(()=>schedulePptRender());pptResizeObserver.observe(pptxHost.value)}
async function renderPptPreview(force=false){const host=pptxHost.value,buffer=pptBuffer.value;if(!host||!buffer||previewKind.value!=='pptx')return;if(pptRendering){pptRenderPending=true;return}const width=Math.max(320,Math.min(1280,Math.floor(host.clientWidth-36)));if(!force&&host.childElementCount&&Math.abs(width-pptRenderedWidth)<12)return;pptRendering=true;pptRenderPending=false;try{host.innerHTML='';const{init}=await import('pptx-preview');const renderer=init(host,{width,height:Math.round(width*9/16),mode:'list'});await renderer.preview(buffer);pptRenderedWidth=width}catch(e){previewError.value='PPTX 自适应预览渲染失败';throw e}finally{pptRendering=false;if(pptRenderPending){pptRenderPending=false;schedulePptRender()}}}
async function applyPreview(data:any){previewUrl.value=data.preview_url;downloadUrl.value=data.download_url;previewKind.value=data.preview_kind;previewError.value=data.preview_error||'';previewText.value='';stopPptObserver();pptBuffer.value=null;pptRenderedWidth=0;if(['text','markdown','docx'].includes(previewKind.value)){const response=await fetch(previewUrl.value);if(!response.ok)throw new Error(previewKind.value==='docx'?'Word 预览加载失败':'文本预览加载失败');previewText.value=await response.text()}else if(previewKind.value==='pptx'){const response=await fetch(downloadUrl.value);if(!response.ok)throw new Error('PPTX 文件加载失败');pptBuffer.value=await response.arrayBuffer();await nextTick();if(!pptxHost.value)throw new Error('PPTX 预览容器未准备好');await renderPptPreview(true);observePptHost()}}
async function loadIngestionMetadata(){if(!selectedDoc.value)return;try{const[c,s]=await Promise.all([api.get(`/teacher/documents/${selectedDoc.value.document_id}/knowledge-candidates`),api.get(`/teacher/documents/${selectedDoc.value.document_id}/structure`)]);candidates.value=c.data;structure.value=s.data;if(!selectedCandidateId.value||!candidates.value.some(x=>x.candidate_id===selectedCandidateId.value))selectedCandidateId.value=filteredCandidates.value[0]?.candidate_id||candidates.value[0]?.candidate_id||'';syncCandidateDraft()}catch(e){fail(e,'知识边界或结构信息加载失败')}}
async function openDocument(job:any){selectedDoc.value=job;outlineMode.value='document';reviewMode.value='candidates';selectedCandidateId.value='';candidateDraftId.value='';candidateDraft.value='';candidateContentMode.value='preview';const[preview,latest]=await Promise.all([api.post(`/documents/${job.document_id}/preview-token`),api.get(`/teacher/documents/${job.document_id}/semantic-analysis/latest`)]);await applyPreview(preview.data);selectedDoc.value={...job,...preview.data};analysis.value=latest.data;page.value=pageInput.value=1;await Promise.all([loadOutline(),loadIngestionMetadata()]);if(selectedCandidate.value)selectCandidate(selectedCandidate.value);await nextTick();reviewWorkspace.value?.scrollIntoView({behavior:'smooth',block:'start'});ElMessage.success('已跳转到下方预览与审核区')}
function syncCandidateDraft(){const candidate=selectedCandidate.value;if(!candidate){candidateDraftId.value='';candidateDraft.value='';return}if(candidateDraftId.value!==candidate.candidate_id){candidateDraftId.value=candidate.candidate_id;candidateDraft.value=candidateCurrentMarkdown(candidate)}}
function selectCandidate(candidate:any){selectedCandidateId.value=candidate.candidate_id;candidateDraftId.value=candidate.candidate_id;candidateDraft.value=candidateCurrentMarkdown(candidate);candidateContentMode.value='preview';const firstPage=Number(candidate.page_start||candidate.source_pages?.[0]||1);go(firstPage)}
async function loadOutline(){if(!courseId.value)return;try{const selectedId=selectedNode.value?.node_id;const params=teachingScopeFilter.value?{class_id:teachingScopeFilter.value}:{};const result=outlineMode.value==='document'&&selectedDoc.value?await api.get(`/teacher/documents/${selectedDoc.value.document_id}/outline`,{params}):await api.get(`/teacher/courses/${courseId.value}/outline`,{params});nodes.value=result.data.nodes;relations.value=result.data.relations;partitions.value=result.data.partitions||[];teachingLevels.value=result.data.teaching_levels||[];if(outlineMode.value==='course'&&(!selectedMaterialType.value||!partitions.value.some(x=>x.material_type===selectedMaterialType.value)))selectedMaterialType.value=partitions.value[0]?.material_type||'';const candidates=visibleNodes.value;selectedNode.value=candidates.find(x=>x.node_id===selectedId&&x.node_type==='knowledge_point')||candidates.find(x=>x.node_type==='knowledge_point')||null;if(selectedNode.value)await sourceForNode(selectedNode.value)}catch(e){fail(e,'目录加载失败')}}
async function sourceForNode(node:any){if(node.node_type!=='knowledge_point'&&!String(node.markdown||'').trim())return;const actual=nodes.value.find(x=>x.node_id===node.node_id)||node;selectedNode.value=actual;const source=actual.sources?.[0];if(!source)return;if(source.document_id!==selectedDoc.value?.document_id){const job=jobs.value.find(x=>x.document_id===source.document_id);const preview=(await api.post(`/documents/${source.document_id}/preview-token`)).data;if(job)selectedDoc.value={...job,...preview};await applyPreview(preview)}page.value=pageInput.value=clampPage(source.page_number,totalPages.value,1)}
async function selectMaterialPartition(value:string){selectedMaterialType.value=value;treeRef.value?.setCheckedKeys([]);selectedNode.value=visibleNodes.value.find(x=>x.node_type==='knowledge_point')||null;await nextTick();if(selectedNode.value)await sourceForNode(selectedNode.value)}
async function analyze(job:any){if(analysisMode.value==='api'&&useOwnApi.value&&!ownAiReady.value)return ElMessage.warning(aiProvider.value==='ollama'?'请填写 Ollama Base URL 和模型':'请完整填写并保存自有 API 配置');const body:any={analysis_mode:analysisMode.value};if(analysisMode.value==='api'&&useOwnApi.value)Object.assign(body,{ai_provider:aiProvider.value,ai_base_url:aiBaseUrl.value,ai_model:aiModel.value,ai_api_key:aiApiKey.value,use_saved_ai:!aiApiKey.value&&hasSavedAiKey.value});try{analysis.value=(await api.post(`/teacher/documents/${job.document_id}/semantic-analysis`,body)).data;aiApiKey.value='';ElMessage.success(analysisMode.value==='api'?'API 语义分析已进入队列（仅读取已落库 DocumentIR）':'仅本地分析已进入队列');await loadJobs()}catch(e){fail(e,'启动分析失败')}}
async function retryParse(job:any){try{await api.post(`/teacher/ingestion-jobs/${job.job_id}/retry`);ElMessage.success('已重新进入文档解析队列');await loadJobs()}catch(e){fail(e,'重新解析失败')}}
async function rebuildPptTitles(job:any){try{await ElMessageBox.confirm('将从原 PPTX 本地重建标题栏、DocumentIR 和待审核候选；不会连接 MinerU，也不会自动调用语义 API。尚未批准的旧候选将被替换。','重建 PPT 标题',{type:'warning',confirmButtonText:'开始重建'});await api.post(`/teacher/ingestion-jobs/${job.job_id}/reparse-presentation`);ElMessage.success('已进入 PPT 本地标题重建队列');await loadJobs()}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'PPT 标题重建失败')}}
async function saveAiSettings(test=false){if(!ownAiReady.value)return ElMessage.warning(aiProvider.value==='ollama'?'请填写 Ollama Base URL 和模型':'请填写 Base URL、模型和 API Key');savingAi.value=true;testingAi.value=test;try{aiSettings.value=(await api.put('/teacher/ai-settings',{provider:aiProvider.value,base_url:aiBaseUrl.value,model:aiModel.value,api_key:aiApiKey.value})).data;aiApiKey.value='';ElMessage.success(aiProvider.value==='ollama'?'Ollama 配置已保存':'自有 API 配置已加密保存');if(test){aiSettings.value=(await api.post('/teacher/ai-settings/test')).data;ElMessage.success('连接测试成功')}}catch(e){try{await loadAiSettings()}catch{}fail(e,test?'配置已保存，但连接测试失败':'保存 API 配置失败')}finally{savingAi.value=false;testingAi.value=false}}
function focusPptSlide(slideNumber:number){if(!pptxHost.value)return;const selectors='.pptx-preview-slide,[data-slide-index],section';const found=Array.from(pptxHost.value.querySelectorAll(selectors)) as HTMLElement[];const direct=Array.from(pptxHost.value.children) as HTMLElement[];const targets=found.length>=slideNumber?found:direct;targets[slideNumber-1]?.scrollIntoView({behavior:'smooth',block:'start'})}
async function saveMaterial(job:any){try{const result=(await api.patch(`/teacher/documents/${job.document_id}/material-metadata`,{material_type:job.material_type,tags:job.tags||[]})).data;ElMessage.success(result.rebuild_status==='queued'?'分类已保存，相关材料分区正在后台更新':'资料用途与标签已确认');await loadJobs();if(outlineMode.value==='course')window.setTimeout(()=>void loadOutline(),800)}catch(e){fail(e,'资料分类保存失败')}}
function clearCandidateRevision(candidate:any){candidateDraftId.value=candidate.candidate_id;candidateDraft.value=candidateOriginalMarkdown(candidate);candidate.teacher_revision='';ElMessage.info('已恢复教材原文，可继续在正文框中编辑')}
async function saveCandidate(candidate:any){try{const edited=candidateDraftId.value===candidate.candidate_id?candidateDraft.value:candidateCurrentMarkdown(candidate);const original=candidateOriginalMarkdown(candidate);const teacherRevision=edited.trim()===original.trim()?'':edited;const result=(await api.patch(`/teacher/knowledge-candidates/${candidate.candidate_id}`,{title:candidate.title,knowledge_type:candidate.knowledge_type,teacher_revision:teacherRevision})).data;Object.assign(candidate,result);candidateDraftId.value=candidate.candidate_id;candidateDraft.value=candidateCurrentMarkdown(candidate);await Promise.all([loadIngestionMetadata(),loadOutline(),loadJobs()]);ElMessage.success(candidate.publishable===false?'修订已保存；该候选仍不可批准发布':'知识候选及源码修订已保存')}catch(e){fail(e,'知识候选保存失败')}}
async function approveCandidate(candidate:any){try{const edited=candidateDraftId.value===candidate.candidate_id?candidateDraft.value:candidateCurrentMarkdown(candidate);await api.patch(`/teacher/knowledge-candidates/${candidate.candidate_id}`,{title:candidate.title,knowledge_type:candidate.knowledge_type,teacher_revision:edited.trim()===candidateOriginalMarkdown(candidate).trim()?'':edited});await api.post(`/teacher/knowledge-candidates/${candidate.candidate_id}/approve`);await Promise.all([loadIngestionMetadata(),loadOutline(),loadJobs()]);const remaining=pendingCandidateCount.value;if(remaining){selectedCandidateId.value=filteredCandidates.value[0]?.candidate_id||'';ElMessage.success(`当前知识点已批准到知识库，已自动进入下一项；本资料还有 ${remaining} 项待审核`)}else{ElMessage.success('本资料的知识点已批准到知识库；请在课程发布区另行发布给学生')}}catch(e){fail(e,'批准知识候选失败')}}
async function rejectCandidate(candidate:any){try{await api.post(`/teacher/knowledge-candidates/${candidate.candidate_id}/reject`);ElMessage.success('候选已驳回');await Promise.all([loadIngestionMetadata(),loadJobs()])}catch(e){fail(e,'驳回知识候选失败')}}
const wholeDocumentReviewReadyFor=(job:any)=>Boolean(
  job
  && ['ready','review_required'].includes(String(job.status||''))
  && !['queued','running','retry_wait','failed'].includes(String(job.analysis_status||''))
)
const wholeDocumentReviewReady=computed(()=>wholeDocumentReviewReadyFor(selectedDoc.value)&&documentWorkflow(selectedDoc.value).can_approve===true)
async function approveWholeDocument(job:any=selectedDoc.value){
  if(completingReview.value||!job||!wholeDocumentReviewReadyFor(job))return ElMessage.warning('请先完成资料解析与语义分析')
  completingReview.value=true
  try{
    await ElMessageBox.confirm(`确认已经预览“${job.original_name||'当前资料'}”整本内容并核对无误？本次将整份资料中符合条件的知识批准到教师知识库。学生需等课程另行发布后才能使用；习题、答案、解析和已排除区域不纳入。`,'批准到知识库',{
      type:'warning',confirmButtonText:'批准到知识库',cancelButtonText:'再检查一下'
    })
    const result=(await api.post(`/teacher/documents/${job.document_id}/approve-to-library`)).data
    Object.assign(job,result,{knowledge_review_status:'approved',knowledge_review_mode:'whole_document'})
    if(selectedDoc.value?.document_id===job.document_id)Object.assign(selectedDoc.value,result,{knowledge_review_status:'approved',knowledge_review_mode:'whole_document'})
    ElMessage.success(`“${job.original_name||'当前资料'}”已批准到知识库，尚未发布本次变更给学生`)
    await loadJobs()
    if(selectedDoc.value?.document_id===job.document_id)await Promise.all([loadIngestionMetadata(),loadOutline()])
  }catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'批准到知识库失败')}
  finally{completingReview.value=false}
}
async function deleteDocument(job:any){try{await ElMessageBox.confirm(`删除“${job.original_name}”及其未发布解析内容？此操作不可撤销。`,'删除错误资料',{type:'warning',confirmButtonText:'确认删除'});await api.delete(`/teacher/documents/${job.document_id}`);if(selectedDoc.value?.document_id===job.document_id){selectedDoc.value=null;selectedNode.value=null;nodes.value=[];previewUrl.value=''}ElMessage.success('错误资料及其解析内容已删除');await loadJobs()}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'删除资料失败')}}
async function deleteSelectedDocuments(){if(!selectedJobs.value.length)return ElMessage.warning('请先勾选资料');try{await ElMessageBox.confirm(`批量删除选中的 ${selectedJobs.value.length} 份资料及其未发布解析内容？`,'批量删除资料',{type:'warning'});const result=(await api.post('/teacher/documents/batch-delete',{ids:selectedJobs.value.map(x=>x.document_id)})).data;if(result.failed.length)ElMessage.warning(`已删除 ${result.deleted.length} 份，${result.failed.length} 份因运行中或已发布而保留`);else ElMessage.success(`已删除 ${result.deleted.length} 份资料`);if(result.deleted.includes(selectedDoc.value?.document_id)){selectedDoc.value=null;selectedNode.value=null;nodes.value=[];previewUrl.value=''}selectedJobs.value=[];await loadJobs()}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'批量删除资料失败')}}
async function analysisAction(action:'retry'|'cancel'){if(!analysis.value)return;try{analysis.value=(await api.post(`/teacher/analysis-jobs/${analysis.value.analysis_job_id}/${action}`)).data}catch(e){fail(e,'任务操作失败')}}
async function saveNode(status?:string){if(!selectedNode.value)return;try{const body={title:selectedNode.value.title,markdown:selectedNode.value.markdown,keywords:selectedNode.value.keywords,parent_id:selectedNode.value.parent_id,sort_order:selectedNode.value.sort_order,...(status?{status}:{}),class_ids:selectedNode.value.class_ids||[]};await api.patch(`/teacher/knowledge-nodes/${selectedNode.value.node_id}`,body);if(body.class_ids.length)rememberTeachingScope(body.class_ids);const isFolder=['chapter','section'].includes(selectedNode.value.node_type);ElMessage.success(status==='rejected'?'内容已驳回并移出目录':status==='approved'&&isFolder?'父节点及其全部子节点已批准':status==='approved'&&outlineMode.value==='document'?'知识点已批准，并同步到候选审核':'知识节点及教学层级已更新');await Promise.all([loadOutline(),loadIngestionMetadata(),loadJobs()])}catch(e){fail(e,'保存失败')}}
async function rejectNode(){if(!selectedNode.value)return;try{const{value}=await ElMessageBox.prompt('请简要填写驳回原因，便于后续恢复和复核。','驳回知识点',{inputPlaceholder:'例如：超纲、重复、识别错误',inputValidator:v=>!!String(v||'').trim()||'请填写驳回原因'});await api.patch(`/teacher/knowledge-nodes/${selectedNode.value.node_id}`,{status:'rejected',reason:value});ElMessage.success('内容已移入独立回收站');await Promise.all([loadOutline(),loadIngestionMetadata(),loadJobs()])}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'驳回失败')}}
async function approveCheckedNodes(){if(outlineMode.value!=='document')return ElMessage.warning('批量批准仅用于文档独立目录');const ids=(treeRef.value?.getCheckedKeys(false)||[]).map(String).filter((id:string)=>nodes.value.some(x=>String(x.node_id)===id&&x.status!=='approved'));if(!ids.length)return ElMessage.warning('请先勾选待批准的目录或知识点');try{const result=(await api.post('/teacher/knowledge-nodes/batch-approve',{ids})).data;treeRef.value?.setCheckedKeys([]);ElMessage.success(`已批准 ${result.approved_leaf_count} 个最小知识点，并同步候选审核`);await Promise.all([loadOutline(),loadIngestionMetadata(),loadJobs()])}catch(e){fail(e,'批量批准失败')}}
async function importCheckedToGraph(){const checked=(treeRef.value?.getCheckedKeys(false)||[]).map(String);const ids=checked.filter((id:string)=>nodes.value.some(x=>String(x.node_id)===id&&x.node_type==='knowledge_point'&&x.status==='approved'));if(!ids.length)return ElMessage.warning('请先勾选已经批准的最小知识点');try{const result=(await api.post(`/teacher/courses/${courseId.value}/knowledge-graph/import-approved-nodes`,{node_ids:ids})).data;ElMessage.success(`已将 ${result.imported} 个已入库知识点同步到图谱草稿；需在图谱页发布给学生`)}catch(e){fail(e,'导入知识图谱失败')}}
async function mergeChecked(){const ids=(treeRef.value?.getCheckedKeys()||[]).filter((id:string)=>nodes.value.find(x=>x.node_id===id)?.node_type==='knowledge_point');if(ids.length<2)return ElMessage.warning('请勾选至少两个知识点');const{value}=await ElMessageBox.prompt('合并后的知识点标题','合并知识点',{inputValue:nodes.value.find(x=>x.node_id===ids[0])?.title});await api.post('/teacher/knowledge-nodes/merge',{node_ids:ids,title:value});await loadOutline()}
async function splitSelected(){const x=selectedNode.value;if(!x||x.node_type!=='knowledge_point')return ElMessage.warning('请选择知识点');const text=x.markdown||'',middle=Math.max(1,Math.floor(text.length/2));await api.post('/teacher/knowledge-nodes/split',{node_id:x.node_id,parts:[{title:`${x.title}（一）`,markdown:text.slice(0,middle)},{title:`${x.title}（二）`,markdown:text.slice(middle)}]});await loadOutline()}
function flattenVisibleTree(items:any[]=treeData.value):any[]{return items.flatMap(item=>[item,...flattenVisibleTree(item.children||[])])}
function checkedDragItems(dragged:any,siblingMove=false){const checked=new Set((treeRef.value?.getCheckedKeys(false)||[]).map(String));if(!checked.has(String(dragged.node_id)))return[dragged];const candidates=flattenVisibleTree().filter(x=>checked.has(String(x.node_id)));if(siblingMove){const type=visibleBranchIdentity(dragged).nodeType;return candidates.filter(x=>visibleBranchIdentity(x).nodeType===type)}return candidates.filter(x=>x.node_type===dragged.node_type)}
function checkedDragIds(dragged:any,siblingMove=false){const ids=checkedDragItems(dragged,siblingMove).map(item=>siblingMove?visibleBranchIdentity(item).nodeId:String(item.node_id));return[...new Set(ids)].sort((left,right)=>{const a=nodes.value.find(x=>String(x.node_id)===left),b=nodes.value.find(x=>String(x.node_id)===right);return Number(a?.sort_order||0)-Number(b?.sort_order||0)})}
function allowTreeDrag(node:any){return node?.data?.status!=='rejected'}
const treeNodeRank=(type:string)=>(({chapter:0,section:1,knowledge_point:2} as Record<string,number>)[type]??99)
function allowTreeDrop(draggingNode:any,dropNode:any,type:'prev'|'inner'|'next'){const dragged=draggingNode.data,target=dropNode.data;if(dragged.material_type!==target.material_type||dragged.generation_id!==target.generation_id)return false;const draggedBranch=visibleBranchIdentity(dragged),targetBranch=visibleBranchIdentity(target),ids=checkedDragIds(dragged,true);if(type==='inner'){const folderId=String(target._dropFolderId||(['chapter','section'].includes(target.node_type)?target.node_id:''));return Boolean(folderId)&&!ids.includes(folderId)}return treeNodeRank(draggedBranch.nodeType)>=treeNodeRank(targetBranch.nodeType)&&!ids.includes(targetBranch.nodeId)}
function treePositionSnapshot(){return visibleNodes.value.map(x=>({node_id:String(x.node_id),parent_id:x.parent_id?String(x.parent_id):null,sort_order:Number(x.sort_order||0)}))}
async function moveTreeNodes(draggingNode:any,dropNode:any,dropType:'before'|'after'|'inner'){const dragged=draggingNode.data,target=dropNode.data,siblingMove=dropType!=='inner',ids=checkedDragIds(dragged,true),draggedBranch=visibleBranchIdentity(dragged),targetBranch=visibleBranchIdentity(target);let targetParentId:string|null=null,targetIndex=0;if(dropType==='inner'){targetParentId=String(target._dropFolderId||target.node_id);targetIndex=nodes.value.filter(x=>x.parent_id===targetParentId&&!ids.includes(String(x.node_id))).length}else if(draggedBranch.nodeType===targetBranch.nodeType){targetParentId=targetBranch.parentId;const siblings=nodes.value.filter(x=>(x.parent_id?String(x.parent_id):null)===targetParentId&&x.material_type===dragged.material_type&&!ids.includes(String(x.node_id))).sort((a,b)=>Number(a.sort_order||0)-Number(b.sort_order||0));const targetPosition=siblings.findIndex(x=>String(x.node_id)===targetBranch.nodeId);targetIndex=Math.max(0,targetPosition+(dropType==='after'?1:0))}const snapshot=treePositionSnapshot(),scopeKey=treeScopeKey.value;try{let createdNodeIds:string[]=[];if(siblingMove&&draggedBranch.nodeType!==targetBranch.nodeType){const result=(await api.post('/teacher/knowledge-nodes/move-visible-siblings',{node_ids:ids,target_node_id:targetBranch.nodeId,position:dropType})).data;createdNodeIds=result.created_node_ids||[]}else await api.post('/teacher/knowledge-nodes/move',{node_ids:ids,target_parent_id:targetParentId,target_index:targetIndex});lastTreeMove.value={scopeKey,placements:snapshot,createdNodeIds};treeRef.value?.setCheckedKeys([]);ElMessage.success(dropType==='inner'?'已将整个目录分支归入目标文件夹，可用“撤销上次移动”恢复':treeNodeRank(draggedBranch.nodeType)>treeNodeRank(targetBranch.nodeType)?'已将子项提升为父级并列项':ids.length>1?`已将 ${ids.length} 个目录分支调整为同级`:'已调整为同级并更新顺序');await loadOutline()}catch(e){fail(e,'移动知识节点失败');await loadOutline()}}
async function undoLastTreeMove(){if(!canUndoTreeMove.value||!lastTreeMove.value)return ElMessage.warning('当前目录没有可撤销的移动');undoingTreeMove.value=true;try{await api.post('/teacher/knowledge-nodes/restore-positions',{placements:lastTreeMove.value.placements,remove_node_ids:lastTreeMove.value.createdNodeIds});lastTreeMove.value=null;treeRef.value?.setCheckedKeys([]);ElMessage.success('已撤销上次目录移动');await loadOutline()}catch(e){fail(e,'撤销目录移动失败')}finally{undoingTreeMove.value=false}}
async function reviewRelation(r:any,status:string){await api.patch(`/teacher/courses/${courseId.value}/knowledge-relations`,{relation_id:r.relation_id,status});await loadOutline()}
async function previewTrash(item:any){const source=item.sources?.[0];if(!source)return ElMessage.warning('该知识点没有可预览的原始来源');const job=jobs.value.find(x=>x.document_id===source.document_id);if(!job)return ElMessage.warning('原始资料已不存在');await openDocument(job);page.value=pageInput.value=clampPage(source.page_number,totalPages.value,1)}
async function restoreTrash(item:any){try{await api.post(`/teacher/knowledge-trash/${item.node_id}/restore`);ElMessage.success('知识点已恢复为待审核');await Promise.all([loadJobs(),loadOutline()])}catch(e){fail(e,'恢复失败')}}
async function deleteTrash(item:any){try{await ElMessageBox.confirm(`永久删除“${item.title}”？此操作不可撤销。`,'彻底删除知识点',{type:'warning'});await api.delete(`/teacher/knowledge-trash/${item.node_id}`);ElMessage.success('知识点已彻底删除');await loadJobs()}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'彻底删除失败')}}
async function deleteSelectedTrash(){if(!selectedTrash.value.length)return ElMessage.warning('请先勾选回收站知识点');try{await ElMessageBox.confirm(`永久删除选中的 ${selectedTrash.value.length} 个知识点？历史发布版本引用的内容会自动保留。`,'批量彻底删除',{type:'warning'});const result=(await api.post('/teacher/knowledge-trash/batch-delete',{ids:selectedTrash.value.map(x=>x.node_id)})).data;if(result.failed.length)ElMessage.warning(`已删除 ${result.deleted.length} 个节点，${result.failed.length} 项受发布版本保护`);else ElMessage.success(`已删除 ${result.deleted.length} 个知识节点`);selectedTrash.value=[];await loadJobs()}catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'批量删除失败')}}
async function approveSelectedToLibrary(){
  if(completingReview.value)return
  const ids=pendingReviewIds.value.length?[...pendingReviewIds.value]:selectedJobs.value.map(job=>job.document_id)
  if(!ids.length)return ElMessage.warning('请先勾选已审查的资料')
  const id=courseId.value
  completingReview.value=true
  try{
    await ElMessageBox.confirm(`确认已核对这 ${ids.length} 份资料？将其中符合条件的知识批准到教师知识库，之后可单独发布给学生。`,'批量批准到知识库',{type:'warning',confirmButtonText:'批准到知识库'})
    pendingReviewIds.value=ids
    const result=(await api.post(`/teacher/courses/${id}/knowledge-library/approve`,{ids})).data
    pendingReviewIds.value=result.failed.map((item:any)=>item.document_id)
    selectedJobs.value=[]
    if(result.failed.length)ElMessage.warning(`已入库 ${result.approved.length} 份，${result.failed.length} 份未完成：${result.failed.map((item:any)=>item.message).join('；')}`)
    else ElMessage.success(`已将 ${result.approved.length} 份资料批准到知识库；可继续发布给学生`)
  }catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'批准未完成，可继续上次入库')}
  finally{completingReview.value=false;await loadJobs()}
}
async function publish(){
  if(completingReview.value||(!readiness.value?.can_publish&&!pendingStudentPublishId.value))return
  const id=courseId.value
  completingReview.value=true
  try{
    await ElMessageBox.confirm('将当前课程全部符合发布条件的已入库知识生成为学生版本，替换之前的学生知识版本。学生按课程授权和教学层级使用；图谱版本在图谱页单独发布。','发布给学生',{type:'warning',confirmButtonText:'发布给学生'})
    pendingStudentPublishId.value ||= crypto.randomUUID()
    const version=(await api.post(`/teacher/courses/${id}/knowledge-versions/publish`,{request_id:pendingStudentPublishId.value})).data
    pendingStudentPublishId.value=''
    ElMessage.success(`课程知识库 v${version.version_number} 已发布给学生`)
    await loadJobs()
  }catch(e:any){if(e!=='cancel'&&e!=='close')fail(e,'发布未完成，可重试本次发布')}
  finally{completingReview.value=false}
}
async function withdrawPublication(){
  if(completingReview.value)return
  const id=courseId.value
  try{
    const versions=(await api.get(`/teacher/courses/${id}/knowledge-versions`)).data
    const version=versions.find((row:any)=>row.status==='published')
    if(!version)return ElMessage.info('当前没有已发布的知识版本')
    await ElMessageBox.confirm(`确认撤回知识版本 v${version.version_number}？学生将暂时不能使用该版本的知识和原文件，已有学习记录保留。`,'撤回指定版本',{type:'warning'})
    completingReview.value=true
    await api.post(`/teacher/courses/${id}/knowledge-versions/${version.version_id}/withdraw`)
    ElMessage.success(`知识版本 v${version.version_number} 已撤回，可修改草稿后重新发布`)
    if(courseId.value===id)await loadJobs()
  }catch(e){if(e!=='cancel'&&e!=='close')fail(e,'撤回失败')}
  finally{completingReview.value=false}
}
function go(v:any=pageInput.value){page.value=pageInput.value=clampPage(v,totalPages.value,page.value);if(previewKind.value==='pptx')nextTick(()=>focusPptSlide(page.value))}
watch(reviewMode,async()=>{if(previewKind.value==='pptx'&&pptBuffer.value){await nextTick();pptRenderedWidth=0;await renderPptPreview(true);observePptHost()}})
onMounted(async()=>{await load();timer=window.setInterval(async()=>{await loadJobs();if(analysis.value&&['queued','running','retry_wait'].includes(analysis.value.status)){analysis.value=(await api.get(`/teacher/analysis-jobs/${analysis.value.analysis_job_id}`)).data;if(analysis.value.status==='review_required')await Promise.all([loadOutline(),loadIngestionMetadata()])}},4000);parserTimer=window.setInterval(()=>void refreshParserStatus(),15000);document.addEventListener('visibilitychange',onParserVisibilityChange)})
onUnmounted(()=>{if(timer)clearInterval(timer);if(parserTimer)clearInterval(parserTimer);document.removeEventListener('visibilitychange',onParserVisibilityChange);stopPptObserver()})
</script>

<template><main class="content knowledge-center">
  <el-alert v-if="selectedJobs.length||pendingReviewIds.length" type="info" :closable="false">
    <span>已选择 {{selectedJobs.length}} 份资料。审查无误后批准到教师知识库，再单独发布给学生。</span>
    <el-button type="success" :loading="completingReview" @click="approveSelectedToLibrary">{{pendingReviewIds.length?'继续上次入库':'批准勾选资料到知识库'}}</el-button>
  </el-alert>
  <div class="teacher-page-topbar workbench-hero">
    <div class="page-title"><h1>知识中心</h1><p class="muted">上传与解析 → 审查 → 批准到知识库 → 发布给学生</p></div>
    <div class="topbar-tools">
    <el-tooltip :content="parserStatusDetail" placement="bottom">
      <span class="service-indicator" :class="parserConnected?'connected':'disconnected'"><i></i>{{parserStatusLabel}}</span>
    </el-tooltip>
    <el-popover placement="bottom-end" :width="390" trigger="click">
      <template #reference>
        <el-button class="api-mode-trigger">
          <span class="api-mode-trigger-copy"><small>内容分析服务</small><strong>{{apiSourceLabel}}</strong></span>
          <el-icon class="api-mode-trigger-arrow" aria-hidden="true"><ArrowDown /></el-icon>
        </el-button>
      </template>
      <div class="api-settings-popover">
        <div class="popover-heading"><div><b>API 调用方式</b><small>控制知识整理与语义分析请求</small></div><el-tag size="small" :type="apiSource==='local'?'info':apiSource==='custom'?'warning':'success'">{{apiSourceLabel}}</el-tag></div>
        <el-select v-model="apiSource" class="api-source-select" aria-label="API 调用方式">
          <el-option label="服务器默认服务（Mock / 云中转 / 管理员接口）" value="server" />
          <el-option label="教师自有接口" value="custom" />
          <el-option label="仅本地规则（不调用 API）" value="local" />
        </el-select>
        <p v-if="apiSource==='server'" class="api-settings-note">无配置时使用确定性 Mock；管理员也可切换到云中转或服务器接口。</p>
        <p v-else-if="apiSource==='custom'" class="api-settings-note">支持 OpenAI 兼容、Gemini 和 Ollama；密钥在服务端加密且不会回显。</p>
        <p v-else class="api-settings-note">不调用外部大模型，仅使用本地规则完成知识整理。</p>
        <div v-if="apiSource==='custom'" class="api-custom-fields">
          <el-select v-model="aiProvider" placeholder="接口协议"><el-option label="OpenAI 兼容接口" value="openai_compatible"/><el-option label="Google Gemini" value="gemini"/><el-option label="本机 Ollama" value="ollama"/></el-select>
          <el-input v-model="aiBaseUrl" placeholder="Base URL，例如 https://api.example.com/v1 或 http://127.0.0.1:11434/v1" />
          <el-input v-model="aiModel" placeholder="模型名称" />
          <el-input v-model="aiApiKey" type="password" show-password autocomplete="off" :disabled="aiProvider==='ollama'" :placeholder="aiProvider==='ollama'?'Ollama 无需 API Key':hasSavedAiKey?'已保存 Key；如不更换可留空':'API Key'" />
          <div class="api-connection-state"><el-tag size="small" :type="aiVerificationType">{{aiVerificationLabel}}</el-tag><span>{{aiSettings.verification_message}}</span></div>
          <div class="api-settings-actions"><el-button :loading="savingAi&&!testingAi" @click="saveAiSettings(false)">保存配置</el-button><el-button type="primary" :loading="testingAi" @click="saveAiSettings(true)">保存并测试连接</el-button><el-button link type="info" @click="aiApiKey=''">清空输入</el-button></div>
        </div>
      </div>
    </el-popover>
    </div>
  </div>
  <el-alert v-if="readiness&&!readiness.can_publish" class="readiness-alert" type="warning" :closable="false"><template #title>知识库尚未达到发布条件</template><span v-for="item in readiness.blockers" :key="item.code">{{item.message}}（{{item.count}}）　</span></el-alert>
  <el-card shadow="never" class="resource-toolbar-card">
    <div class="resource-toolbar">
      <div class="resource-course-select"><span class="toolbar-label">当前课程</span><el-select v-model="courseId" :disabled="uploading||completingReview" @change="changeCourse"><el-option v-for="x in courses" :key="x.course_id" :label="x.course_name" :value="x.course_id"/></el-select></div>
      <div class="resource-toolbar-copy"><span class="toolbar-kicker">课程知识建设</span><strong>资料、解析和审核集中管理</strong><small>平时只显示两项处理状态，需要时再打开上传进度或知识点回收站。</small></div>
      <div class="resource-toolbar-actions"><el-button type="primary" @click="uploadDialogVisible=true">上传资料</el-button><el-button plain :disabled="!jobs.length" @click="progressDialogVisible=true">查看处理进度</el-button><el-button plain @click="trashDialogVisible=true">知识点回收站 <span class="toolbar-count">{{trash.length}}</span></el-button><el-button circle @click="loadJobs" aria-label="刷新"><span class="refresh-symbol">↻</span></el-button></div>
    </div>
  </el-card>
  <el-dialog v-model="uploadDialogVisible" title="上传课程资料" width="min(620px, calc(100% - 32px))" class="knowledge-dialog" :close-on-click-modal="false" :close-on-press-escape="!uploading" :show-close="!uploading">
    <div class="upload-dialog-body">
      <div class="upload-dialog-intro"><span class="dialog-icon">↑</span><div><b>将资料加入当前课程</b><p>支持 PDF、DOCX、PPTX、Markdown 和 TXT。上传后会自动进入文档解析流程。</p></div></div>
      <div class="upload-file"><span class="toolbar-label">单个文件</span><el-upload v-model:file-list="uploadFiles" :auto-upload="false" :limit="1" :on-exceed="replaceUploadFile" :disabled="uploading" accept=".pdf,.docx,.pptx,.md,.markdown,.txt"><el-button plain :disabled="uploading">选择文件</el-button></el-upload><small v-if="file" class="selected-file">已选择：{{file.name}}</small></div>
      <div class="upload-folder-divider"><span>或</span></div>
      <label class="folder-picker upload-folder-picker"><input ref="folderInput" type="file" multiple webkitdirectory :disabled="uploading" @change="chooseFolder"/>选择整个文件夹导入</label><small v-if="folderFiles.length" class="selected-file">已识别 {{folderFiles.length}} 个支持文件</small>
      <div class="upload-dialog-actions"><el-button :disabled="uploading" @click="uploadDialogVisible=false">取消</el-button><el-button type="primary" :loading="uploading" :disabled="!file" @click="upload">上传并入库</el-button><el-button type="success" plain :loading="uploading" :disabled="!folderFiles.length" @click="uploadFolder">整包本地导入</el-button></div>
    </div>
  </el-dialog>
  <el-dialog v-model="progressDialogVisible" title="资料处理进度" width="min(760px, calc(100% - 32px))" class="knowledge-dialog" destroy-on-close>
    <div class="progress-dialog-intro"><span class="dialog-icon">◷</span><div><b>文档解析与 内容分析</b><p>处理会在后台继续，列表中的状态标签会同步更新。</p></div></div>
    <div v-if="jobs.length" class="progress-board progress-dialog-board"><div v-for="job in jobs" :key="`progress-${job.job_id}`" class="progress-row"><div class="progress-file"><strong>{{job.original_name}}</strong><small>{{currentStage(job)}}</small></div><div class="progress-tags"><el-tag size="small" :type="documentStatusType(job)">{{documentStatusLabel(job)}}</el-tag><el-tag size="small" :type="analysisStatusType(job)">{{analysisStatusLabel(job)}}</el-tag></div><el-progress :percentage="overallPercent(job)" :stroke-width="9" :status="job.analysis_status==='failed'?'exception':undefined"/><small v-if="job.failed_pages" class="progress-error">异常 {{job.failed_pages}} 页</small></div></div>
    <el-empty v-else description="当前课程还没有资料" :image-size="70"/>
  </el-dialog>
  <el-dialog v-model="trashDialogVisible" title="知识点回收站" width="min(980px, calc(100% - 32px))" class="knowledge-dialog trash-dialog" destroy-on-close>
    <div class="trash-dialog-intro"><span class="dialog-icon">⌫</span><div><b>被驳回的知识点</b><p>这里的内容与正式知识树隔离，可以恢复为待审核，或彻底删除。</p></div><div class="trash-dialog-count"><strong>{{trash.length}}</strong><small>项</small></div></div>
    <div class="trash-dialog-actions"><el-button type="danger" plain :disabled="!selectedTrash.length" @click="deleteSelectedTrash">批量彻底删除（{{selectedTrash.length}}）</el-button><el-button @click="loadJobs">刷新</el-button></div>
    <el-empty v-if="!trash.length" description="暂无被驳回的知识点"/><el-table v-else :data="trash" @selection-change="selectedTrash=$event"><el-table-column type="selection" width="46"/><el-table-column prop="title" label="知识点" min-width="220"/><el-table-column label="类型" width="110"><template #default="s"><el-tag>{{s.row.node_type}}</el-tag></template></el-table-column><el-table-column prop="reason" label="进入原因" min-width="180"/><el-table-column prop="original_name" label="来源资料" min-width="160"><template #default="s">{{s.row.original_name||s.row.sources?.[0]?.original_name||'课程统一知识树'}}</template></el-table-column><el-table-column prop="trashed_at" label="时间" width="165"/><el-table-column label="操作" width="220"><template #default="s"><el-button link @click="previewTrash(s.row)">预览来源</el-button><el-button link type="success" @click="restoreTrash(s.row)">恢复</el-button><el-button link type="danger" @click="deleteTrash(s.row)">彻底删除</el-button></template></el-table-column></el-table>
  </el-dialog>
  <el-card shadow="never" class="publication-card">
    <template #header><b>课程知识发布</b></template>
    <el-steps :active="readiness?.student_publication?3:libraryDocumentCount?2:0" finish-status="success" align-center>
      <el-step title="审查" description="核对原文与知识点，保存修订"/>
      <el-step title="批准到知识库" :description="`已入库或部分入库 ${libraryDocumentCount} 份资料`"/>
      <el-step title="发布给学生" :description="readiness?.student_publication?`当前学生版本 v${readiness.student_publication.version_number} · ${readiness.student_knowledge_points} 个知识点`:'尚未发布学生版本'"/>
    </el-steps>
    <p class="muted">发布范围是整门课程中符合条件的已入库知识。批准与修订不改变学生当前版本；知识图谱从已入库知识同步草稿，再单独发布图谱版本。</p>
    <div class="header-actions">
      <el-button type="success" :loading="completingReview" :disabled="!readiness?.can_publish&&!pendingStudentPublishId" @click="publish">{{pendingStudentPublishId?'重试发布给学生':'发布给学生'}}</el-button>
      <el-button :disabled="completingReview||!readiness?.student_publication" @click="withdrawPublication">撤回学生知识版本</el-button>
      <el-button @click="router.push({path:'/knowledge-graph',query:{course:courseId}})">进入知识图谱</el-button>
    </div>
    <el-alert v-if="readiness?.blockers?.length" type="warning" :closable="false" title="发布前需要处理">
      <p v-for="blocker in readiness.blockers" :key="blocker.code">{{blocker.message}}（{{blocker.count}}）</p>
    </el-alert>
  </el-card>
  <el-card shadow="never" class="job-card"><template #header><div class="card-header"><div class="card-header-copy"><b>资料、用途与语义分析</b><div class="muted">系统按内容建议用途，教师确认后用于课程知识组织；状态标签会显示文档解析与 内容分析的中文进度。</div></div><div class="header-actions"><el-button type="danger" plain :disabled="!selectedJobs.length" @click="deleteSelectedDocuments">批量删除（{{selectedJobs.length}}）</el-button></div></div></template><el-table ref="jobsTable" :key="courseId" class="jobs-table" :data="jobs" row-key="document_id" @selection-change="selectedJobs=$event"><el-table-column type="selection" :reserve-selection="true" width="46"/><el-table-column prop="original_name" label="资料" min-width="190"/><el-table-column label="资料用途" min-width="160"><template #default="s"><el-select v-model="s.row.material_type" size="small"><el-option v-for="item in materialTypes" :key="item[0]" :label="item[1]" :value="item[0]"/></el-select><small class="classification-hint">{{s.row.classification_status==='confirmed'?'教师已确认':`系统建议：${materialLabel(s.row.suggested_material_type)}`}}</small></template></el-table-column><el-table-column label="分类标签" min-width="190"><template #default="s"><el-select v-model="s.row.tags" multiple filterable allow-create default-first-option size="small" placeholder="输入标签并回车"/></template></el-table-column><el-table-column label="文档解析" width="130"><template #default="s"><el-tag size="small" :type="documentStatusType(s.row)">{{documentStatusLabel(s.row)}}</el-tag></template></el-table-column><el-table-column label="内容分析" width="140"><template #default="s"><el-tag size="small" :type="analysisStatusType(s.row)">{{analysisStatusLabel(s.row)}}</el-tag></template></el-table-column><el-table-column label="知识库状态" min-width="150"><template #default="s"><el-tag :type="documentWorkflow(s.row).library_status==='approved'?'success':'info'">{{libraryLabel(s.row)}}</el-tag></template></el-table-column><el-table-column label="学生版本" min-width="150"><template #default="s"><span>{{documentWorkflow(s.row).student_version?`v${documentWorkflow(s.row).student_version} 含本资料`:'尚未发布'}}</span></template></el-table-column><el-table-column label="学生查看原文件" width="150"><template #default="s"><el-switch :model-value="Boolean(s.row.student_file_visible)" :loading="visibilitySaving.includes(s.row.document_id)" :aria-label="`允许学生预览 ${s.row.original_name}`" @change="setStudentFileVisible(s.row,Boolean($event))"/><small class="classification-hint">随知识发布后可预览</small></template></el-table-column><el-table-column label="操作" width="570" fixed="right"><template #default="s"><div class="job-row-actions"><el-button link type="primary" @click="openDocument(s.row)">审查</el-button><el-button link type="success" @click="saveMaterial(s.row)">确认分类</el-button><el-button link type="warning" :disabled="completingReview||!documentWorkflow(s.row).can_approve" @click="approveWholeDocument(s.row)">批准到知识库</el-button><el-button v-if="s.row.status==='failed'||(Number(s.row.document_block_count||0)===0&&s.row.status==='review_required')" link type="warning" @click="retryParse(s.row)">重新解析</el-button><el-button v-if="String(s.row.original_name||'').toLowerCase().endsWith('.pptx')" link type="warning" :disabled="['queued','running'].includes(s.row.status)" @click="rebuildPptTitles(s.row)">重建PPT标题</el-button><el-button link type="primary" :disabled="!['ready','review_required'].includes(s.row.status)||['queued','running'].includes(s.row.analysis_status)||Number(s.row.document_block_count||0)===0" @click="analyze(s.row)">重新分析</el-button><el-button link type="danger" @click="deleteDocument(s.row)">删除</el-button></div></template></el-table-column></el-table></el-card>
  <el-alert v-if="analysis" :type="analysis.status==='failed'?'error':analysis.warnings?.length?'warning':'info'" :closable="false"><template #title>内容分析：{{analysisStatusLabel(analysis)}} · 阶段 {{analysisStageLabel(analysis.current_stage)}} · {{analysis.current_batch}}/{{analysis.total_batches}} 批 · 已调用 {{analysis.api_calls}} 次</template><p class="analysis-source-note">输入来源：已落库文档内容；重新分析不会重新连接文档解析服务器。</p><p v-if="analysis.error_message">{{analysis.error_message}}</p><p v-for="warning in analysis.warnings||[]" :key="warning" class="analysis-warning">{{warning}}</p><el-tag v-if="analysis.status==='failed'&&!analysis.retryable" type="danger">额度或权限错误不会自动重试；已有调用次数包含此前成功批次</el-tag><el-button v-if="analysis.status==='failed'&&analysis.retryable" @click="analysisAction('retry')">断点重试</el-button><el-button v-if="['queued','running','retry_wait'].includes(analysis.status)" @click="analysisAction('cancel')">取消</el-button></el-alert>
  <el-card v-if="selectedDoc" shadow="never" class="candidate-card"><template #header><div class="card-header"><div><b>知识点审查工作区</b><div class="muted">候选与文档独立目录共用同一批最小知识点和审核状态；当前知识点正文会直接载入编辑框，可在原文基础上增删改并保存，来源 block 仍用于追溯。</div></div><div class="candidate-summary"><el-tag type="warning">{{pendingCandidateCount}} 待审核</el-tag><el-tag type="success">{{approvedCandidateCount}} 已入库</el-tag><el-tag type="info">{{candidates.length}} 个最小知识点</el-tag></div></div></template><el-alert v-if="structure?.warnings?.length" type="warning" :closable="false" class="structure-warning"><span v-for="warning in structure.warnings" :key="warning.message">{{warning.message}}；</span></el-alert><div class="review-mode-bar"><span class="muted">当前审核范围</span><el-radio-group v-model="reviewMode" size="small"><el-radio-button value="candidates">知识点候选（{{pendingCandidateCount}}）</el-radio-button><el-radio-button value="outline">知识结构治理</el-radio-button></el-radio-group><span class="muted">审查时核对原文、修订内容；批准到知识库后，再到课程发布区发布给学生。</span><div class="document-review-actions"><el-tag v-if="selectedDoc.knowledge_review_status==='approved'" type="success">整本已批准到知识库</el-tag><el-button type="primary" plain :disabled="completingReview||!wholeDocumentReviewReady" @click="approveWholeDocument()">整本批准到知识库</el-button></div></div></el-card>
  <section v-if="selectedDoc&&reviewMode==='candidates'" ref="reviewWorkspace" class="structured-review candidate-review">
    <div class="outline-pane candidate-queue-pane">
      <div class="review-pane-heading"><b>最小知识点队列</b><el-tag size="small" type="info">{{filteredCandidates.length}}</el-tag></div>
      <el-radio-group v-model="candidateFilter" size="small" class="candidate-filter"><el-radio-button value="pending">待审核</el-radio-button><el-radio-button value="approved">已入库</el-radio-button><el-radio-button value="rejected">已驳回</el-radio-button><el-radio-button value="all">全部</el-radio-button></el-radio-group>
      <el-scrollbar v-if="filteredCandidates.length" class="candidate-list">
        <button v-for="candidate in filteredCandidates" :key="candidate.candidate_id" type="button" class="candidate-list-item" :class="{active:selectedCandidate?.candidate_id===candidate.candidate_id}" @click="selectCandidate(candidate)">
          <span class="candidate-list-title"><strong>{{candidate.title||'待命名知识点'}}</strong><el-tag size="small" :type="reviewStatusType(candidate.review_status)">{{reviewStatusLabel(candidate.review_status)}}</el-tag></span>
          <span class="candidate-list-excerpt">{{candidate.section_excerpt||'暂无实质正文'}}</span>
          <span class="candidate-list-meta">{{candidateChapterPath(candidate)}} · 第 {{candidate.page_start||'—'}}-{{candidate.page_end||candidate.page_start||'—'}} 页</span>
          <span class="candidate-list-meta">{{knowledgeTypeLabel(candidate.knowledge_type)}} · {{candidate.source_block_ids?.length||0}} 个原文 block · 置信度 {{Math.round(Number(candidate.confidence||0)*100)}}%</span>
        </button>
      </el-scrollbar>
      <el-empty v-else description="当前筛选下没有候选；可切换“全部”检查已入库或已驳回内容" :image-size="72"/>
    </div>
    <div class="source-review">
      <div class="page-selector"><el-button v-if="previewKind==='pdf'" :disabled="page<=1" @click="go(page-1)">上一页</el-button><el-input-number v-if="previewKind==='pdf'" v-model="pageInput" :controls="false" :min="1" :max="totalPages" @keyup.enter="go()"/><el-button v-if="previewKind==='pdf'" @click="go()">跳转</el-button><strong v-if="previewKind==='pdf'">{{page}} / {{totalPages}}</strong><el-button v-if="previewKind==='pdf'" :disabled="page>=totalPages" @click="go(page+1)">下一页</el-button><el-tag v-else-if="previewKind==='pptx'" type="success">浏览器内 PPTX 预览</el-tag><el-tag v-else-if="previewKind==='docx'" type="success">浏览器内 Word 预览</el-tag><el-button tag="a" :href="downloadUrl" target="_blank">下载原文件</el-button></div><iframe v-if="previewKind==='pdf'&&pdfUrl" :key="pdfUrl" :src="pdfUrl" title="原文件对照"/><iframe v-else-if="previewKind==='docx'&&previewText" sandbox="" :srcdoc="previewText" title="Word 原文件对照"/><div v-else-if="previewKind==='pptx'" ref="pptxHost" class="pptx-preview-host"/><div v-else-if="['markdown','text'].includes(previewKind)" class="rendered-markdown source-markdown" v-html="markdownHtml"/><el-result v-else icon="warning" title="预览不可用" :sub-title="previewError||'请下载原文件查看'"/>
    </div>
    <div class="candidate-editor">
      <template v-if="selectedCandidate">
        <div class="candidate-editor-heading"><div><h3>{{selectedCandidate.title||'待命名知识点'}}</h3><span class="muted">{{candidateChapterPath(selectedCandidate)}} · 来源第 {{selectedCandidate.page_start||'—'}}-{{selectedCandidate.page_end||selectedCandidate.page_start||'—'}} 页</span></div><el-tag :type="reviewStatusType(selectedCandidate.review_status)">{{reviewStatusLabel(selectedCandidate.review_status)}}</el-tag></div>
        <el-alert type="info" :closable="false" class="source-first-alert">当前条目是文档独立目录中带完整正文的最小子节；仅含标题的目录占位不会进入候选队列。通过或驳回会同步更新知识树。</el-alert>
        <div class="candidate-meta-grid"><div><small>知识点类型</small><el-select v-model="selectedCandidate.knowledge_type" size="small"><el-option v-for="item in knowledgeTypes" :key="item[0]" :label="item[1]" :value="item[0]"/></el-select></div><div><small>边界置信度</small><strong>{{Math.round(Number(selectedCandidate.confidence||0)*100)}}%</strong></div><div><small>原文块</small><strong>{{selectedCandidate.source_block_ids?.length||0}} 个</strong></div></div>
        <el-input v-model="selectedCandidate.title" class="candidate-title-input" placeholder="知识点标题"/>
        <div class="markdown-mode-bar"><span>知识点正文</span><el-radio-group v-model="candidateContentMode" size="small"><el-radio-button value="preview">预览</el-radio-button><el-radio-button value="source">源码 / 修订</el-radio-button></el-radio-group></div>
        <div v-if="candidateContentMode==='preview'" class="rendered-markdown candidate-preview" v-html="candidatePreviewHtml"/>
        <div v-else class="candidate-source-editor"><div class="source-revision-editor"><div class="source-baseline-heading"><b>知识点正文（可编辑）</b><small>已载入当前正文，可直接在现有内容基础上增删改</small></div><el-input v-model="candidateDraft" type="textarea" :autosize="{minRows:16,maxRows:32}" maxlength="50000" show-word-limit placeholder="这里会载入当前知识点正文；支持 Markdown / LaTeX"/><div class="source-revision-toolbar"><el-button size="small" @click="clearCandidateRevision(selectedCandidate)">恢复教材原文</el-button><span>局部删除：选中文字后按 Delete / Backspace；完成后点击“保存审查修订”</span></div></div></div>
        <div class="source-block-list"><div class="source-block-list-heading"><b>来源追溯</b><small>点击页码可定位原文</small></div><button v-for="block in selectedCandidate.source_blocks||[]" :key="block.block_id" type="button" class="source-block-item" @click="go(Number(block.page_number||1))"><span><el-tag size="small">第 {{block.page_number||'—'}} 页</el-tag><code>{{block.block_id}}</code></span><small>{{block.block_type}} · {{block.region_type||'knowledge'}}</small></button></div>
        <el-alert v-if="selectedCandidate.publishable===false" type="warning" :closable="false" class="candidate-policy-alert" title="该内容属于习题/答案/解析类文字，可以继续编辑保存，但不得批准或发布到知识库；请转到习题中心单独审核。"/>
        <div class="candidate-actions"><el-button :disabled="selectedCandidate.review_status==='REJECTED'" @click="saveCandidate(selectedCandidate)">保存审查修订</el-button><el-button type="success" :disabled="selectedCandidate.publishable===false||selectedCandidate.review_status==='APPROVED'" @click="approveCandidate(selectedCandidate)">批准到知识库</el-button><el-button type="danger" plain :disabled="selectedCandidate.review_status==='REJECTED'" @click="rejectCandidate(selectedCandidate)">驳回</el-button><small class="muted">批准仅更新教师知识库；学生继续使用上次发布版本，直到再次发布给学生。</small></div>
      </template>
      <el-empty v-else description="请从左侧选择一个知识点候选"/>
    </div>
  </section>
  <section v-if="selectedDoc&&reviewMode==='outline'" class="structured-review governance-review">
    <div class="graph-export-bar"><span>勾选已入库的最小知识点，同步到图谱草稿后可继续审查和发布。</span><el-button type="success" plain @click="importCheckedToGraph">同步到图谱草稿</el-button></div>
    <div class="teaching-scope-bar"><div class="teaching-scope-intro"><b>知识点教学层级</b><small>未设置层级的知识点自动并入课程默认知识库；设置后会记住到该知识点。</small><el-tag size="small" :type="selectedNodeIsCourseWide?'info':selectedNode?.class_ids?.length?'success':'info'">{{selectedNodeIsCourseWide?'默认知识库':selectedNode?.class_ids?.length?`已设置 ${selectedNode.class_ids.length} 个教学层级`:'请选择知识点'}}</el-tag></div><el-select v-model="teachingScopeFilter" clearable placeholder="查看全部教学层级" @change="loadOutline"><el-option label="仅课程通用知识点" value="course_wide"/><el-option v-for="item in teachingLevels" :key="item.class_id" :label="teachingLevelLabel(item)" :value="item.class_id"/></el-select><el-select v-model="selectedNodeClassIds" multiple clearable :disabled="!selectedNode||selectedNode.node_type!=='knowledge_point'" placeholder="设置当前知识点适用层级"><el-option v-for="item in teachingLevels" :key="item.class_id" :label="teachingLevelLabel(item)" :value="item.class_id"/></el-select><div class="teaching-scope-actions"><el-button type="primary" :disabled="!selectedNode||selectedNode.node_type!=='knowledge_point'" @click="saveNode()">保存教学层级</el-button><el-button v-if="selectedNodeIsCourseWide&&rememberedTeachingScopeIds.length" plain @click="applyRememberedTeachingScope">沿用上次层级</el-button><small v-if="selectedNodeIsCourseWide&&rememberedTeachingScopeLabel">上次设置：{{rememberedTeachingScopeLabel}}</small></div></div>
     <div class="outline-pane"><el-radio-group v-model="outlineMode" @change="loadOutline"><el-radio-button value="document">文档独立目录</el-radio-button><el-radio-button value="course">课程统一目录</el-radio-button></el-radio-group><div v-if="outlineMode==='course'" class="material-partitions"><button v-for="item in partitions" :key="item.material_type" type="button" class="material-partition" :class="{active:selectedMaterialType===item.material_type}" @click="selectMaterialPartition(item.material_type)"><span>{{item.label}}</span><b>{{item.knowledge_point_count}}</b><small>{{item.pending_review_count}} 待审核</small><em v-if="item.unconfirmed_document_count">{{item.unconfirmed_document_count}} 份待确认</em><em v-else-if="item.rebuild_status==='safe_fallback'">安全降级</em></button></div><el-empty v-if="outlineMode==='course'&&!partitions.length" description="当前课程还没有可治理的材料分区" :image-size="54"/><div class="tree-actions"><el-button v-if="outlineMode==='document'" size="small" type="success" @click="approveCheckedNodes">勾选项批准到知识库</el-button><el-button size="small" @click="mergeChecked">合并勾选知识点</el-button><el-button size="small" @click="splitSelected">拆分当前知识点</el-button><el-button size="small" :disabled="!canUndoTreeMove" :loading="undoingTreeMove" @click="undoLastTreeMove">撤销上次移动</el-button></div><p class="tree-drag-hint"><b>拖到条目上/下边缘：并列并排序；拖到条目中间：归入该目录。</b> 文档独立目录可勾选多个目录或知识点一次批准，审核状态会同步到候选队列。</p><el-tree :empty-text="jobs.length ? '当前范围暂无知识树；请在资料列表完成分类、分析与审核，或切换筛选范围' : '尚未上传课程资料'" ref="treeRef" :data="treeData" node-key="node_id" show-checkbox check-strictly default-expand-all highlight-current draggable :allow-drag="allowTreeDrag" :allow-drop="allowTreeDrop" @node-drop="moveTreeNodes" @node-click="sourceForNode"><template #default="{data}"><span class="tree-node-label"><span class="tree-node-icon">{{data.node_type==='knowledge_point'?'◆':'▸'}}</span><span>{{data.label||data.title}}</span><el-tag size="small">{{knowledgeNodeStatusLabel(data.status)}}</el-tag></span></template></el-tree></div>
     <div class="knowledge-editor"><template v-if="selectedNode"><div class="knowledge-breadcrumb">{{selectedBreadcrumb}}</div><div class="block-meta"><el-tag v-if="outlineMode==='course'">{{materialLabel(selectedNode.material_type)}}</el-tag><el-tag>{{knowledgeNodeTypeLabel(selectedNode.node_type)}}</el-tag><el-tag>{{knowledgeNodeStatusLabel(selectedNode.status)}}</el-tag></div><el-input v-model="selectedNode.title" placeholder="标题"/><el-input v-model="selectedNode.markdown" type="textarea" :autosize="{minRows:8,maxRows:24}" placeholder="知识点原文 Markdown"/><el-divider>Markdown / LaTeX 审批预览</el-divider><div class="rendered-markdown" v-html="selectedMarkdownHtml"/><el-select v-if="selectedNode.node_type==='knowledge_point'" v-model="selectedNode.parent_id" placeholder="所属节"><el-option v-for="s in sections" :key="s.node_id" :label="s.title" :value="s.node_id"/></el-select><div class="block-actions"><el-button title="仅保存当前修订，不批准或发布" @click="saveNode()">保存审查修订</el-button><el-button type="success" title="保存当前修订并批准入库，无需先点保存" @click="saveNode('approved')">批准到知识库</el-button><el-button type="danger" plain @click="rejectNode">驳回</el-button></div></template><el-empty v-else description="请选择一个带正文的知识点；空文件夹无需审核"/><el-divider>知识关系</el-divider><div v-for="r in relations.filter(x=>outlineMode!=='course'||!selectedMaterialType||x.material_type===selectedMaterialType)" :key="r.relation_id" class="relation-row"><span>{{r.source_title}} — {{relationTypeLabel(r.relation_type)}} → {{r.target_title}}</span><el-tag>{{knowledgeNodeStatusLabel(r.status)}}</el-tag><el-button size="small" @click="reviewRelation(r,'approved')">确认</el-button><el-button size="small" @click="reviewRelation(r,'rejected')">驳回</el-button></div></div>
    <div class="source-review"><div class="page-selector"><el-button v-if="previewKind==='pdf'" :disabled="page<=1" @click="go(page-1)">上一页</el-button><el-input-number v-if="previewKind==='pdf'" v-model="pageInput" :controls="false" :min="1" :max="totalPages" @keyup.enter="go()"/><el-button v-if="previewKind==='pdf'" @click="go()">跳转</el-button><strong v-if="previewKind==='pdf'">{{page}} / {{totalPages}}</strong><el-button v-if="previewKind==='pdf'" :disabled="page>=totalPages" @click="go(page+1)">下一页</el-button><el-tag v-else-if="previewKind==='pptx'" type="success">浏览器内 PPTX 预览</el-tag><el-tag v-else-if="previewKind==='docx'" type="success">浏览器内 Word 预览</el-tag><el-button tag="a" :href="downloadUrl" target="_blank">下载原文件</el-button></div><iframe v-if="previewKind==='pdf'&&pdfUrl" :key="pdfUrl" :src="pdfUrl" title="原文件对照"/><iframe v-else-if="previewKind==='docx'&&previewText" sandbox="" :srcdoc="previewText" title="Word 原文件对照"/><div v-else-if="previewKind==='pptx'" ref="pptxHost" class="pptx-preview-host"/><div v-else-if="['markdown','text'].includes(previewKind)" class="rendered-markdown source-markdown" v-html="markdownHtml"/><el-result v-else icon="warning" title="预览不可用" :sub-title="previewError||'请下载原文件查看'"/></div>
  </section>
</main></template>

<style scoped>
.rendered-markdown :deep(.katex-display){max-width:100%;overflow-x:auto;overflow-y:hidden;padding:4px 0;text-align:left}
.rendered-markdown :deep(.katex-display>.katex){text-align:left}
.knowledge-center{container-name:knowledge-center;container-type:inline-size;min-width:0;overflow-x:clip;--workspace-height:clamp(620px,calc(100dvh - 120px),920px)}
.teacher-page-topbar{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:24px}
.teacher-page-topbar .page-title{min-width:0}
.topbar-tools{display:flex;align-items:center;justify-content:flex-end;gap:10px;min-width:0;margin-top:10px}
.service-indicator{display:inline-flex;align-items:center;gap:8px;min-height:36px;padding:8px 12px;border:1px solid var(--border-subtle);border-radius:8px;background:var(--surface);color:var(--text-secondary);font:400 12px/1.5 var(--campus-font);white-space:nowrap}
.service-indicator i{width:6px;height:6px;flex-shrink:0;border-radius:50%;background:#68705e}
.service-indicator.connected i{background:#526747}
.service-indicator.disconnected i{background:#a16b31}
.api-mode-trigger.el-button{height:auto;min-height:54px;width:216px;max-width:100%;padding:9px 13px;border-color:var(--border-subtle);background:var(--surface);color:var(--text-primary);font-family:var(--campus-font)}
.api-mode-trigger :deep(>span){width:100%;display:flex;align-items:center;justify-content:space-between;gap:20px}
.api-mode-trigger.el-button:hover{border-color:#8d9c80;background:#edf0e7;color:#294b3c}
.api-mode-trigger-copy{display:grid;gap:3px;min-width:0;text-align:left;line-height:1.4}
.api-mode-trigger-copy small{font-size:11px;font-weight:400;color:var(--text-secondary)}
.api-mode-trigger-copy strong{font-size:13px;font-weight:600;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.api-mode-trigger-arrow{flex-shrink:0;font-size:14px;color:var(--text-secondary)}
.api-settings-popover{display:grid;gap:13px}
.popover-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.popover-heading div{display:grid;gap:3px}
.popover-heading small{font-size:12px;color:#8490a3}
.api-source-select{width:100%}
.api-settings-note{margin:0;padding:9px 10px;border-radius:8px;background:#f5f8fc;color:#68758a;font-size:12px;line-height:1.55}
.api-custom-fields{display:grid;gap:10px;padding-top:2px}
.api-custom-fields .el-button{justify-self:start;padding:0}
.api-connection-state{display:flex;align-items:flex-start;gap:8px;color:#68758a;font-size:12px;line-height:1.45}.api-connection-state span{flex:1}
.api-settings-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.api-settings-actions .el-button{justify-self:auto;padding:8px 12px}
.resource-toolbar-card{margin-top:16px;border:1px solid #dce9e5;background:linear-gradient(135deg,#ffffff 0%,#f5fbf9 100%)}
.resource-toolbar{display:grid;grid-template-columns:minmax(220px,.7fr) minmax(240px,1fr) auto;align-items:center;gap:22px}
.resource-course-select{display:grid;gap:7px;min-width:0}.resource-course-select .el-select{width:100%}
.resource-toolbar-copy{display:grid;gap:3px;min-width:0}.toolbar-kicker{color:#23746f;font-size:11px;font-weight:700;letter-spacing:.12em}.resource-toolbar-copy strong{color:#173e49;font-size:16px}.resource-toolbar-copy small{color:#71817f;line-height:1.45}
.resource-toolbar-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.toolbar-count{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;margin-left:3px;padding:0 5px;border-radius:999px;background:#edf7f4;color:#23746f;font-size:11px}.refresh-symbol{font-size:18px;line-height:1}
.knowledge-dialog :deep(.el-dialog){overflow:hidden;border-radius:18px;box-shadow:0 24px 70px rgba(23,62,73,.2)}.knowledge-dialog :deep(.el-dialog__header){margin:0;padding:20px 24px 14px;border-bottom:1px solid #edf2f1;background:#fbfefd}.knowledge-dialog :deep(.el-dialog__title){color:#173e49;font-weight:750}.knowledge-dialog :deep(.el-dialog__body){padding:20px 24px 24px}.upload-dialog-body{display:grid;gap:16px}.upload-dialog-intro,.progress-dialog-intro,.trash-dialog-intro{display:flex;align-items:flex-start;gap:12px;padding:14px;border-radius:12px;background:#f1faf7;color:#315d57}.dialog-icon{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;flex:0 0 34px;border-radius:10px;background:#d9f0e9;color:#23746f;font-size:20px;font-weight:700}.upload-dialog-intro div,.progress-dialog-intro div,.trash-dialog-intro div{display:grid;gap:4px;min-width:0}.upload-dialog-intro p,.progress-dialog-intro p,.trash-dialog-intro p{margin:0;color:#71817f;font-size:12px;line-height:1.55}.upload-dialog-body .upload-file{padding:14px;border:1px solid #e5eeeb;border-radius:12px;background:#fff}.selected-file{color:#23746f;font-size:12px}.upload-folder-divider{display:flex;align-items:center;gap:10px;color:#a0aaa8;font-size:12px}.upload-folder-divider:before,.upload-folder-divider:after{content:'';height:1px;flex:1;background:#e7efed}.upload-folder-picker{display:flex;justify-content:center;padding:13px;border:1px dashed #9bc7bc;border-radius:11px;background:#f8fcfb;color:#23746f;font-weight:650;cursor:pointer}.upload-folder-picker:hover{background:#edf7f4;border-color:#378f81}.upload-dialog-actions{display:flex;justify-content:flex-end;gap:8px;padding-top:4px}.progress-dialog-intro{margin-bottom:8px}.progress-dialog-board{margin:0}.progress-dialog-board .progress-row{grid-template-columns:minmax(160px,1fr) minmax(190px,auto) minmax(150px,1fr) auto;gap:12px;padding:13px 0}.progress-file{display:grid;gap:4px;min-width:0}.progress-file strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.progress-file small{color:#71817f}.progress-tags{display:flex;flex-wrap:wrap;gap:6px}.trash-dialog-intro{align-items:center}.trash-dialog-count{display:flex!important;align-items:baseline;gap:3px;margin-left:auto;padding:6px 12px;border-radius:10px;background:#fff}.trash-dialog-count strong{font-size:22px;color:#23746f}.trash-dialog-count small{color:#71817f}.trash-dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin:14px 0 8px}
.upload-card{margin-top:16px}
.upload-toolbar{display:grid;grid-template-columns:minmax(220px,.85fr) minmax(320px,1.35fr) auto;align-items:end;gap:16px}
.upload-course,.upload-file{display:grid;min-width:0;gap:7px}.toolbar-label{color:#78839b;font-size:12px;font-weight:600}.upload-file :deep(.el-upload){max-width:100%}.upload-file :deep(.el-button){max-width:100%}.upload-file :deep(.el-button>span){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.upload-actions,.header-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;min-width:max-content}.trash-card .card-header>.header-actions{display:flex}.upload-actions :deep(.el-button+.el-button),.header-actions :deep(.el-button+.el-button){margin-left:0}
.card-header{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:16px}.card-header-copy{display:grid;min-width:0;gap:5px}.card-header-copy .muted{line-height:1.5}
.job-card{margin-top:16px}.job-card :deep(.el-table),.trash-card :deep(.el-table){width:100%}.job-row-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));align-items:center;gap:2px 8px}.job-row-actions :deep(.el-button){width:100%;margin:0;justify-content:center}.jobs-table :deep(.el-tag){max-width:125px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.candidate-summary{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.review-mode-bar{display:grid;grid-template-columns:max-content max-content minmax(0,1fr) max-content;align-items:center;gap:12px;padding-top:14px;border-top:1px solid #edf0f5}
.review-mode-bar .muted:last-child{min-width:0;text-align:right;font-size:12px}
.document-review-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}
.review-pane-heading,.candidate-editor-heading,.source-block-list-heading,.source-baseline-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.review-pane-heading{margin-bottom:12px}
.candidate-filter{display:flex;flex-wrap:wrap;margin-bottom:12px}
.candidate-list{height:calc(100% - 86px);min-height:470px}
.candidate-list-item{display:grid;width:100%;gap:5px;padding:12px 10px;border:0;border-bottom:1px solid #edf0f5;background:transparent;color:#25334a;text-align:left;cursor:pointer}
.candidate-list-item:hover,.candidate-list-item.active{background:#edf7f4}
.candidate-list-item.active{box-shadow:inset 3px 0 #23746f}
.candidate-list-title{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.candidate-list-title strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.candidate-list-excerpt{display:-webkit-box;overflow:hidden;color:#4d5b70;font-size:12px;line-height:1.55;-webkit-box-orient:vertical;-webkit-line-clamp:2}
.teaching-scope-bar{grid-column:1/-1;display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,320px) minmax(260px,420px) auto;gap:12px;align-items:center;padding:12px 14px;border:1px solid #dbe9e5;border-radius:12px;background:#f2faf8}.teaching-scope-bar div{display:grid;gap:3px}.teaching-scope-bar small{color:#6f817c}.teaching-scope-intro{min-width:0}.teaching-scope-intro .el-tag{justify-self:start;margin-top:2px}.teaching-scope-actions{display:flex!important;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.teaching-scope-actions small{max-width:180px;font-size:11px;line-height:1.35}
.candidate-list-meta{color:#78839b;font-size:12px;line-height:1.4}
.candidate-editor{height:var(--workspace-height);min-width:0;min-height:0;padding:18px;background:white;border:1px solid #e6eaf2;border-radius:12px;overflow:auto;scrollbar-gutter:stable}
.candidate-editor-heading h3{margin:0 0 5px;color:#173b42}
.source-first-alert{margin:16px 0}
.candidate-meta-grid{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:10px;margin-bottom:12px}
.candidate-meta-grid>div{display:grid;gap:5px;padding:9px 10px;border:1px solid #edf0f5;border-radius:8px;background:#fbfcff}
.candidate-meta-grid small,.source-baseline-heading small,.source-block-list-heading small{color:#78839b;font-size:11px}
.candidate-meta-grid strong{font-size:16px;color:#23746f}
.candidate-title-input{margin-bottom:14px}
.markdown-mode-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0}
.candidate-preview{min-height:220px;max-height:430px}
.candidate-source-editor{display:grid;gap:12px}
.source-baseline{padding:11px;border:1px solid #e6eaf2;border-radius:8px;background:#fafbfe}
.source-baseline-heading{align-items:center;margin-bottom:8px}
.source-baseline pre{max-height:220px;margin:0;overflow:auto;white-space:pre-wrap;color:#566277;font:12px/1.6 Consolas,"Microsoft YaHei",monospace}
.source-block-list{display:grid;gap:7px;margin-top:14px;padding-top:12px;border-top:1px solid #edf0f5}
.source-block-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid #e6eaf2;border-radius:7px;background:#fff;color:#526078;text-align:left;cursor:pointer}
.source-block-item:hover{border-color:#79aaa4;background:#f2faf8}
.source-block-item span{display:flex;align-items:center;gap:7px}.source-block-item code{font-size:11px}.source-block-item small{color:#78839b}
.candidate-actions{position:sticky;bottom:-18px;z-index:4;display:flex;justify-content:flex-end;gap:8px;margin:16px -18px -18px;padding:12px 18px;border-top:1px solid #e6eaf2;background:rgba(255,255,255,.96);backdrop-filter:blur(8px)}
.candidate-actions :deep(.el-button+.el-button){margin-left:0}
.candidate-review{grid-template-columns:minmax(230px,.72fr) minmax(0,1.15fr) minmax(0,1fr);grid-template-areas:"queue source editor"}
.candidate-review .candidate-queue-pane{grid-area:queue}.candidate-review .source-review{grid-area:source}.candidate-review .candidate-editor{grid-area:editor}
.governance-review{grid-template-areas:"outline editor source"}.governance-review .outline-pane{grid-area:outline}.governance-review .knowledge-editor{grid-area:editor}.governance-review .source-review{grid-area:source}
.candidate-review>.candidate-queue-pane,.candidate-review>.source-review,.candidate-review>.candidate-editor,.governance-review>.outline-pane,.governance-review>.knowledge-editor,.governance-review>.source-review{height:var(--workspace-height)}
.knowledge-editor .block-actions{position:sticky;bottom:-14px;z-index:4;justify-content:flex-end;flex-wrap:wrap;margin:14px -14px -14px;padding:12px 14px;border-top:1px solid #e6eaf2;background:rgba(255,255,255,.96);backdrop-filter:blur(8px)}
.source-review .page-selector{min-height:42px;margin:8px 0;white-space:nowrap}.source-review :deep(.page-selector .el-button+.el-button){margin-left:0}
.source-review>iframe{display:block;width:100%;height:calc(100% - 58px);min-height:520px;border:1px solid #e1e6ee;border-radius:8px;background:#fff}
.tree-drag-hint{margin:0 0 10px;padding:8px 9px;border-radius:7px;background:#f5f8fc;color:#6f7b90;font-size:12px;line-height:1.5}
.tree-node-label{display:inline-flex;align-items:center;min-width:0;gap:6px}.tree-node-label>span:nth-child(2){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tree-node-icon{color:#6b83b7;font-size:11px}
.material-partitions{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px;margin:12px 0}.material-partition{display:grid;grid-template-columns:1fr auto;gap:2px 8px;min-width:0;padding:9px 10px;border:1px solid #dce9e5;border-radius:9px;background:#fff;color:#526b65;text-align:left;cursor:pointer}.material-partition:hover,.material-partition.active{border-color:#79aaa4;background:#edf7f4;box-shadow:0 0 0 1px rgba(35,116,111,.08)}.material-partition span{font-weight:700;color:#274b46}.material-partition b{color:#23746f}.material-partition small{font-size:11px;color:#6f817c}.material-partition em{grid-column:1/-1;font-size:10px;font-style:normal;color:#b26a00}.knowledge-breadcrumb{margin-bottom:10px;padding:8px 10px;border-radius:7px;background:#f2faf8;color:#526b65;font-size:12px;line-height:1.5}
.progress-board{margin:16px 0}
.progress-row{display:grid;grid-template-columns:minmax(180px,1fr) auto minmax(220px,2fr) auto;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #edf0f5}
.progress-row:last-child{border-bottom:0}
.progress-error{color:#d93025}
.candidate-card{margin:16px 0}.candidate-card .structure-warning{margin-bottom:12px}.analysis-source-note{color:#66758b;font-size:12px}
.progress-row strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@container knowledge-center (max-width:1180px){.candidate-review,.governance-review{grid-template-columns:minmax(240px,.72fr) minmax(0,1.28fr)}.candidate-review{grid-template-areas:"queue source" "editor editor"}.governance-review{grid-template-areas:"outline editor" "source source"}.candidate-review .candidate-editor{height:auto;min-height:540px}.governance-review .source-review{height:min(720px,78dvh)}.progress-row{grid-template-columns:minmax(160px,1fr) auto minmax(220px,1.5fr)}.progress-dialog-board .progress-row{grid-template-columns:minmax(150px,1fr) auto minmax(150px,1fr)}.upload-toolbar{grid-template-columns:minmax(210px,.8fr) minmax(280px,1.2fr)}.upload-actions{grid-column:1/-1;justify-content:flex-end}.resource-toolbar{grid-template-columns:minmax(180px,.8fr) minmax(220px,1fr)}.resource-toolbar-actions{grid-column:1/-1;justify-content:flex-end}}
@container knowledge-center (max-width:760px){.teacher-page-topbar{grid-template-columns:1fr;gap:8px}.topbar-tools{justify-content:space-between;width:100%;margin-top:0}.service-indicator{flex:0 0 auto}.api-mode-trigger{width:min(240px,100%)}.upload-toolbar{grid-template-columns:1fr}.upload-actions{grid-column:auto;justify-content:stretch}.upload-actions :deep(.el-button){flex:1}.resource-toolbar{grid-template-columns:1fr;gap:14px}.resource-toolbar-actions{grid-column:auto;justify-content:stretch}.resource-toolbar-actions :deep(.el-button){flex:1}.resource-toolbar-actions :deep(.el-button.is-circle){flex:0 0 38px}.card-header{grid-template-columns:1fr}.header-actions{min-width:0;justify-content:flex-start;flex-wrap:wrap}.review-mode-bar{grid-template-columns:1fr}.review-mode-bar .muted:last-child{text-align:left}.candidate-review,.governance-review{grid-template-columns:1fr}.candidate-review{grid-template-areas:"queue" "editor" "source"}.governance-review{grid-template-areas:"outline" "editor" "source"}.candidate-review .candidate-editor,.governance-review .source-review{height:auto;min-height:480px}.candidate-meta-grid{grid-template-columns:1fr}.candidate-actions{justify-content:stretch}.candidate-actions :deep(.el-button){flex:1;margin:0}.progress-row{grid-template-columns:minmax(112px,auto) minmax(0,1fr);grid-template-areas:"name name" "parse-label parse-progress" "analysis-label analysis-progress"}.progress-dialog-board .progress-row{grid-template-columns:1fr;gap:8px}.job-row-actions{grid-template-columns:repeat(2,minmax(0,1fr))}.source-block-item{align-items:flex-start;flex-direction:column}.source-block-item code{overflow-wrap:anywhere}.trash-dialog-count{margin-left:auto}.teaching-scope-actions{justify-content:stretch}.teaching-scope-actions :deep(.el-button){flex:1}.teaching-scope-actions small{width:100%;max-width:none}}
 .source-revision-editor{display:grid;gap:8px;padding:14px;border:1px solid #b9ddd3;border-radius:10px;background:#f2faf7}.source-revision-editor :deep(.el-textarea__inner){min-height:220px;padding:12px;background:#fff;color:#294d49;font:13px/1.7 ui-monospace,SFMono-Regular,Consolas,"Microsoft YaHei",monospace}.source-revision-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#617773;font-size:12px}.candidate-policy-alert{margin-top:14px}.candidate-actions{flex-wrap:wrap;align-items:center}.resource-release-panel{display:grid;gap:10px;margin-top:16px;padding:14px 16px;border:1px solid #b9ddd3;border-radius:12px;background:#f2faf7}.resource-release-panel>div:first-child{display:grid;gap:4px}.resource-release-panel span{color:#6b817d;font-size:12px}.resource-release-actions{display:flex;flex-wrap:wrap;gap:8px}.resource-release-actions .el-button{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@container knowledge-center (max-width:760px){.source-revision-toolbar{align-items:flex-start;flex-direction:column}}
</style>
