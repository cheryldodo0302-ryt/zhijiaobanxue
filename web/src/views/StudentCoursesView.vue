<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import StudyArtwork from "../components/StudyArtwork.vue";
import StudyCardDeck from "../components/StudyCardDeck.vue";
import StudentLearningFocus from "../components/StudentLearningFocus.vue";
import PaperWorkspace from "../components/PaperWorkspace.vue";
import { questionOptions } from "../question-display";
import ExpandableList from "../components/ExpandableList.vue";
import { vStudyMotion } from "../study-motion";
import { ArrowDown, Reading, Plus, Collection, Document, FullScreen, Close } from "@element-plus/icons-vue";
import { saveStudentDraft, readStudentDraft } from "../student-navigation";
import { createCardSpeech } from "../card-speech";
import { useAuthStore } from "../stores/auth";
import KnowledgeGraphCanvas from "../components/KnowledgeGraphCanvas.vue";
import KnowledgeMarkdown from "../components/KnowledgeMarkdown.vue";
import StudentMaterialPreview from "../components/StudentMaterialPreview.vue";
import { buildMaterialTree, type MaterialTreeNode } from "../material-tree";
import { readWorkspace, writeWorkspace } from "../workspace-storage";
import {
  learningModeLabel,
  questionAnswerLabel,
  questionTypeLabel,
} from "../question-display";
import {
  startAudioMonitor,
  stopAudioMonitor,
  type AudioMonitorHandle,
} from "../audio-monitor";
import {
  normalizeStudentView,
  uploadStageLabel,
  type UploadState,
} from "../workspace-state";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const courses = ref<any[]>([]);
const courseId = ref("");
const activeTab = ref("qa");
const learningWorkspace = ref<HTMLElement | null>(null);
function resumeLearning() {
  const root = learningWorkspace.value;
  if (!root) return;
  root.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  const pane = [...root.querySelectorAll<HTMLElement>('.el-tab-pane')].find(element => getComputedStyle(element).display !== 'none');
  const target = pane?.querySelector<HTMLElement>('textarea:not(:disabled), input:not(:disabled), button:not(:disabled)');
  (target || root).focus({ preventScroll: true });
}
const loading = ref(false);
const aiStatus = ref<any>(null);
const uploadState = ref<UploadState>({
  stage: "idle",
  progress: 0,
  message: "",
});
const uploadStatusText = computed(() => uploadStageLabel(uploadState.value));
const monitorRunning = ref(false);
let monitorHandle: AudioMonitorHandle | null = null;
const question = ref("");
const studentReply = ref("");
const session = ref<any>(null);
const sourcePreview = ref<any>(null);
const sourceExpanded = ref(false);
const courseNavOpen = ref(false);
const cardView = ref('deck');
const courseGroups = computed(() => [
  { type: 'personal_course', title: '个人课程', detail: '个人课程，仅自己可见' },
  { type: 'shared_course', title: '教师共享课程', detail: '已授权的课程资料' },
].map(group => ({ ...group, items: courses.value.filter(course => course.course_type === group.type) })));
const recentSources = computed(() => {
  const latest = [...messages.value].reverse().find(message => message.role === 'assistant' && message.sources?.length);
  return sourceLocations(latest?.sources || session.value?.sources || []);
});
function chooseSidebarCourse(id: string) {
  courseId.value = id;
  courseNavOpen.value = false;
  navigateCourse();
}
async function showCourseCreation() {
  activeTab.value = 'materials';
  courseNavOpen.value = false;
  await nextTick();
  {
    const input = document.querySelector<HTMLInputElement>('#personal-newCourseName-1, #personal-newCourseName-3');
    input?.scrollIntoView({ block: 'center', behavior: 'instant' });
    input?.focus();
  }
}
watch(() => sourcePreview.value?.document_id, () => { sourceExpanded.value = false });
function sourceLocations(sources:any[] = []) {
  const result:any[] = [];
  for (const source of sources) {
    const locations = source.locations?.length ? source.locations : [source];
    for (const location of locations) {
      if (!location.document_id) continue;
      const item = {...location, section: location.section || source.section};
      if (!result.some(x => x.document_id === item.document_id && x.page_number === item.page_number)) result.push(item);
    }
  }
  return result;
}

const messages = ref<any[]>([]);
const quiz = ref<any>(null);
const responses = ref<any[]>([]);
const grade = ref<any>(null);
const profile = ref<any>(null);
const retrievalMaterial = ref("all");
const documents = ref<any[]>([]);
const previewDocumentId = ref("");
const materialTreeProps = { children: "children", label: "label" };
const sharedMaterialTree = computed(() => buildMaterialTree(documents.value));
const previewTreeNodeId = computed(() => {
  for (const category of sharedMaterialTree.value) {
    for (const tag of category.children || []) {
      const document = (tag.children || []).find(item => item.document_id === previewDocumentId.value);
      if (document) return document.id;
    }
  }
  return "";
});
function selectMaterialTreeNode(data: MaterialTreeNode) {
  if (data.kind === "document" && data.document_id) previewDocumentId.value = data.document_id;
}
watch(documents, (items) => {
  if (!items.some(item => item.document_id === previewDocumentId.value))
    previewDocumentId.value = items[0]?.document_id || "";
});
const blocks = ref<any[]>([]);
const dashboard = ref<any>(null);
const newCourseName = ref("");
const newCourseDescription = ref("");
const materialName = ref("学习材料");
const materialText = ref("");
const documentFile = ref<File | null>(null);
const imageFile = ref<File | null>(null);
const questionBankFile = ref<File | null>(null);
const editingBlock = ref<any>(null);
const editTitle = ref("");
const editKeywords = ref("");
const editContent = ref("");
const splitPosition = ref<number | undefined>(undefined);
const aiSplitDialog = ref(false);
const aiSplitLoading = ref(false);
const aiSplitApplying = ref(false);
const aiSplitTargetId = ref<number | undefined>(undefined);
const aiSplitPreview = ref<any>(null);
const trainingBlockId = ref<number | undefined>(undefined);
const extraKeywords = ref("");
const cloze = ref<any>(null);
const clozeResponses = ref<string[]>([]);
const clozeResult = ref<any>(null);
const recitedText = ref("");
const recitationSubmission = ref({key:'',id:''});
const recitationResult = ref<any>(null);
const speechRate = ref(1);
const speech = "speechSynthesis" in window
  ? createCardSpeech(window.speechSynthesis, undefined, () => ElMessage.warning("朗读中断，请重试或检查浏览器语音设置"))
  : null;
const speechState = speech?.state ?? ref("idle");
const recording = ref(false);
const audioUrl = ref("");
let recorder: MediaRecorder | null = null;
let recorderStream: MediaStream | null = null;
let audioChunks: BlobPart[] = [];
const memoryQuestions = ref<any[]>([]);
const memoryResponses = ref<any[]>([]);
const memoryGrade = ref<any>(null);
const questionCount = ref(6);
const publishedFolders = ref<any[]>([]);
const publishedFolderId = ref("");
const publishedBank = ref<any>(null);
const publishedPaperTitle = ref("课程试卷");
const publishedResponses = ref<any[]>([]);
const publishedGrade = ref<any>(null);
const publishedGraph = ref<any>(null);
const graphSelectedNode = ref<any>(null);
const graphSearch = ref("");
const graphLayout = ref<"force" | "circular">("force");
const publishedKnowledge = ref<any>(null);
const publishedKnowledgeDialog = ref(false);
const selectedPublishedNodeIds = ref<string[]>([]);
const importingPublished = ref(false);
const startingReview = ref(false);
let preferencesReady = false;

function restoreStudyPreferences() {
  const value = readWorkspace<any>(auth.user?.user_id || "", `student:${courseId.value}`, {});
  activeTab.value = normalizeStudentView(
    route.query.view || value?.view || activeTab.value,
    selectedCourse.value?.course_type === "shared_course",
  );
  questionCount.value = Math.max(3, Math.min(12, Math.round(Number(value?.questionCount) || 6)));
  speechRate.value = Math.max(0.75, Math.min(2, Number(value?.speechRate) || 1));
  preferencesReady = true;
}

async function startReview() {
  if (startingReview.value || !courseId.value) return;
  startingReview.value = true;
  const id = courseId.value;
  try {
    await invoke("knowledge_blocks_build", {}, id);
    if (courseId.value !== id) return;
    await loadCourseData();
    if (courseId.value !== id) return;
    if (!blocks.value.length) return ElMessage.info("当前没有可复习知识点");
    activeTab.value = "training";
    await generateCloze();
  } catch (error) { showError(error, "复习准备失败，已完成的卡片会保留，可再次继续"); }
  finally { startingReview.value = false; }
}

function rememberedCourseKey() {
  return auth.user?.user_id
    ? `zhijiao:student-course:${auth.user.user_id}`
    : "";
}

function readRememberedCourse() {
  const key = rememberedCourseKey();
  if (!key) return "";
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function rememberCourse(id = courseId.value) {
  const key = rememberedCourseKey();
  if (!key) return;
  try {
    if (id) localStorage.setItem(key, id);
    else localStorage.removeItem(key);
  } catch {
    // 浏览器禁用本地存储时不影响课程使用。
  }
}

const selectedCourse = computed(() =>
  courses.value.find((item) => item.course_id === courseId.value),
);
const materialPartitions = computed(
  () => selectedCourse.value?.material_partitions || [],
);
const trainingBlock = computed(() =>
  blocks.value.find((item) => item.block_id === trainingBlockId.value),
);
const trainingIndex = computed(() =>
  trainingBlock.value ? blocks.value.findIndex((item) => item.block_id === trainingBlock.value.block_id) : -1,
);
const splitAt = computed(() =>
  Math.max(0, Math.min(editContent.value.length, Number(splitPosition.value || 0))),
);
const splitBefore = computed(() => editContent.value.slice(0, splitAt.value));
const splitAfter = computed(() => editContent.value.slice(splitAt.value));

const recentLearningAttempts = computed(() => {
  const memory = (dashboard.value?.memory_attempts || []).map((attempt: any) => ({
    ...attempt,
    source: "memory",
  }));
  const practice = (dashboard.value?.practice_attempts || []).map(
    (attempt: any) => ({
      ...attempt,
      source: "practice",
    }),
  );
  const published = (dashboard.value?.published_attempts || []).map(
    (attempt: any) => ({
      ...attempt,
      source: "published",
    }),
  );
  return [...memory, ...practice, ...published]
    .sort((left, right) => {
      const leftTime = Date.parse(
        String(left.created_at || "").replace(" ", "T"),
      );
      const rightTime = Date.parse(
        String(right.created_at || "").replace(" ", "T"),
      );
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) {
        return rightTime - leftTime;
      }
      return Number(right.attempt_id || 0) - Number(left.attempt_id || 0);
    })
    .slice(0, 10);
});

function learningAttemptLabel(attempt: any) {
  if (attempt.source === "published") return "教师发布试卷";
  if (attempt.source === "practice") return "专项练习";
  if (attempt.mode === "cloze") return "挖空练习";
  if (attempt.mode === "recitation") return "背诵检测";
  return "记忆训练";
}

function learningAttemptTitle(attempt: any) {
  return (
    attempt.title ||
    (attempt.source === "published"
      ? "教师发布试卷"
      : attempt.source === "practice"
        ? "综合练习"
        : "知识记忆训练")
  );
}

function learningScoreTone(score: any) {
  const value = Number(score);
  return value >= 80 ? "good" : value >= 60 ? "normal" : "low";
}

function formatLearningDate(value: any) {
  const raw = String(value || "");
  if (!raw) return "时间未知";
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : raw;
}

function actionScope(id = courseId.value) {
  return id ? { course_id: id } : {};
}

async function invoke(
  action: string,
  input: Record<string, any> = {},
  id = courseId.value,
) {
  if (!auth.user) throw new Error("登录状态已失效，请重新登录");
  const actorId = auth.user.user_id;
  const requiresCourse = ![
    "personal_course_create",
    "available_courses_list",
  ].includes(action);
  if (requiresCourse && !id) throw new Error("请先选择课程");
  const { data } = await api.post("/agent/invoke", {
    request_id: `web_${Date.now()}_${action}`,
    agent: "student_assistant",
    action,
    actor: { user_id: auth.user.user_id, role: "student" },
    scope: actionScope(id),
    input,
    context: { source: "vue-student", language: "zh-CN" },
  }, { timeout: 130000 });
  if (auth.user?.user_id !== actorId || (requiresCourse && courseId.value !== id))
    throw new Error('课程或账号已切换，已忽略上一页面的响应');
  if (data.status !== "success") throw new Error(data.message || "操作失败");
  return data.data;
}

function showError(error: any, fallback: string) {
  ElMessage.error(error?.response?.data?.detail || error?.message || fallback);
}

async function loadCourses() {
  loading.value = true;
  try {
    courses.value = (await api.get("/student/courses")).data;
    const requestedCourse = String(route.query.course || "");
    const rememberedCourse = readRememberedCourse();
    if (!courseId.value && courses.value.length) {
      const preferredCourse = [requestedCourse, rememberedCourse].find((id) =>
        courses.value.some((item) => item.course_id === id),
      );
      courseId.value = preferredCourse || courses.value[0].course_id;
    }
    if (!courses.value.some((item) => item.course_id === courseId.value))
      courseId.value = courses.value[0]?.course_id || "";
    rememberCourse();
    await courseChanged();
    if (navigationReady && String(route.query.course || '') !== courseId.value) {
      await router.replace({ query: learningQuery() });
      syncSourcePreview();
    }
  } catch (error) {
    showError(error, "课程加载失败");
  } finally {
    loading.value = false;
  }
}

let loadedCourseId = "";
let navigationReady = false;
const draftOwner = auth.user?.user_id || "";
function saveCurrentDraft() {
  if (auth.user?.user_id !== draftOwner) return;
  saveStudentDraft(draftOwner, loadedCourseId, {
    question: question.value, studentReply: studentReply.value, session: session.value,
    messages: messages.value, quiz: quiz.value, responses: responses.value, grade: grade.value,
    retrievalMaterial: retrievalMaterial.value,
  });
}
function restoreCurrentDraft() {
  const draft = readStudentDraft(draftOwner, courseId.value);
  question.value = draft?.question || "";
  studentReply.value = draft?.studentReply || "";
  session.value = draft?.session || null;
  messages.value = draft?.messages || [];
  quiz.value = draft?.quiz || null;
  responses.value = draft?.responses || [];
  grade.value = draft?.grade || null;
  retrievalMaterial.value = draft?.retrievalMaterial || "all";
}
onBeforeUnmount(saveCurrentDraft);
async function courseChanged() {
  if (loadedCourseId === courseId.value) {
    await loadCourseData();
    await loadPublishedGraph();
    return;
  }
  saveCurrentDraft();
  loadedCourseId = courseId.value;
  rememberCourse();
  restoreStudyPreferences();
  retrievalMaterial.value = "all";
  session.value = null;
  messages.value = [];
  quiz.value = null;
  grade.value = null;
  publishedBank.value = null;
  publishedGrade.value = null;
  trainingBlockId.value = undefined;
  documents.value = []; blocks.value = []; profile.value = null; dashboard.value = null;
  cloze.value = null; clozeResult.value = null; recitationResult.value = null;
  memoryQuestions.value = []; memoryGrade.value = null;
  activeTab.value = normalizeStudentView(
    activeTab.value,
    selectedCourse.value?.course_type === "shared_course",
  );
  restoreCurrentDraft();
  await loadCourseData();
  await loadPublishedGraph();
}

async function loadPublishedGraph() {
  publishedGraph.value = null;
  graphSelectedNode.value = null;
  if (!courseId.value || selectedCourse.value?.course_type !== "shared_course")
    return;
  const id = courseId.value;
  try {
    const result = await api.get(`/student/courses/${id}/knowledge-graph`);
    if (courseId.value === id) publishedGraph.value = result.data;
  } catch (error: any) {
    if (error?.response?.status !== 404)
      showError(error, "课程知识图谱加载失败");
  }
}

async function loadCourseData() {
  if (!courseId.value) return;
  const id = courseId.value;
  try {
    const [docs, nextBlocks, nextProfile, nextDashboard] = await Promise.all([
      invoke("document_status", {}, id),
      invoke("knowledge_blocks_list", {}, id),
      invoke("learning_profile", {}, id),
      invoke("student_dashboard", {}, id),
    ]);
    if (courseId.value !== id) return;
    documents.value = docs || [];
    blocks.value = nextBlocks || [];
    profile.value = nextProfile || null;
    dashboard.value = nextDashboard || null;
    if (!trainingBlockId.value && blocks.value.length)
      trainingBlockId.value = blocks.value[0].block_id;
  } catch (error) {
    showError(error, "学习数据加载失败");
  }
}

async function openPublishedKnowledgeDialog() {
  if (selectedCourse.value?.course_type !== "shared_course") return;
  try {
    publishedKnowledge.value = await invoke("published_knowledge_list");
    const items = publishedKnowledge.value?.items || [];
    selectedPublishedNodeIds.value = items
      .filter((item: any) => !item.imported)
      .map((item: any) => String(item.node_id));
    publishedKnowledgeDialog.value = true;
  } catch (error) {
    showError(error, "已发布知识点加载失败");
  }
}

async function importPublishedKnowledge() {
  if (!selectedPublishedNodeIds.value.length)
    return ElMessage.warning("请至少选择一个未导入的知识点");
  importingPublished.value = true;
  try {
    const result = await invoke("published_knowledge_import", {
      node_ids: selectedPublishedNodeIds.value,
    });
    await loadCourseData();
    publishedKnowledgeDialog.value = false;
    ElMessage.success(
      `已导入 ${result.imported_count} 张知识卡片${result.skipped_count ? `，跳过重复卡片 ${result.skipped_count} 张` : ""}`,
    );
  } catch (error) {
    showError(error, "教师知识点导入失败");
  } finally {
    importingPublished.value = false;
  }
}

async function loadProfile() {
  if (!courseId.value) return;
  try {
    profile.value = await invoke("learning_profile");
  } catch (error) {
    showError(error, "学习画像加载失败");
  }
}

async function startGuidance() {
  if (!question.value.trim()) return ElMessage.warning("请先输入课程问题");
  loading.value = true;
  try {
    const result = await invoke("course_qa", {
      question: question.value.trim(),
      intent: "start",
      retrieval_scope: retrievalMaterial.value === "all" ? "all" : "material",
      material_type:
        retrievalMaterial.value === "all" ? null : retrievalMaterial.value,
    });
    session.value = result;
    messages.value = [
      { role: "student", content: question.value.trim() },
      { role: "assistant", content: result.reply, sources: result.sources || [] },
    ];
    quiz.value = null;
    grade.value = null;
  } catch (error) {
    showError(error, "问答启动失败");
  } finally {
    loading.value = false;
  }
}

async function guidedTurn(intent: "respond" | "hint" | "reveal" | "end") {
  if (!session.value) return;
  const content =
    intent === "respond"
      ? studentReply.value.trim()
      : intent === "hint"
        ? "我暂时没有思路，请给我一点提示。"
        : intent === "reveal"
          ? "请根据课程证据总结答案。"
          : "结束本题。";
  if (intent === "respond" && !content)
    return ElMessage.warning("请先写下你的想法");
  loading.value = true;
  try {
    const result = await invoke("course_qa", {
      question: question.value.trim(),
      intent,
      student_message: content,
      session_id: session.value.session_id,
    });
    messages.value.push(
      { role: "student", content },
      { role: "assistant", content: result.reply, sources: result.sources || [] },
    );
    session.value = result;
    studentReply.value = "";
    if (result.completed) await loadProfile();
  } catch (error) {
    showError(error, "本轮引导失败");
  } finally {
    loading.value = false;
  }
}

async function generateQuiz() {
  loading.value = true;
  try {
    quiz.value = await invoke("quiz_generate", {
      question_id: session.value?.question_id,
    });
    responses.value = quiz.value.items.map(() => "");
    grade.value = null;
  } catch (error) {
    showError(error, "练习生成失败");
  } finally {
    loading.value = false;
  }
}

async function submitQuiz() {
  loading.value = true;
  try {
    grade.value = await invoke("quiz_submit", {
      paper_id: quiz.value.paper_id,
      question_id: quiz.value.question_id,
      items: quiz.value.items,
      responses: responses.value,
    });
    await loadProfile();
  } catch (error) {
    showError(error, "练习提交失败");
  } finally {
    loading.value = false;
  }
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(reader.error || new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}

function chooseFile(
  event: Event,
  target: "document" | "image" | "questionBank",
) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  if (target === "document") documentFile.value = file;
  if (target === "image") imageFile.value = file;
  if (target === "questionBank") questionBankFile.value = file;
}

async function createPersonalCourse() {
  if (!newCourseName.value.trim()) return ElMessage.warning("请填写课程名称");
  loading.value = true;
  try {
    const created = await invoke(
      "personal_course_create",
      {
        course_name: newCourseName.value.trim(),
        description: newCourseDescription.value.trim(),
      },
      "",
    );
    newCourseName.value = "";
    newCourseDescription.value = "";
    await loadCourses();
    if (created?.course_id) {
      await router.push({ query: learningQuery(created.course_id, "materials") });
    }
    ElMessage.success("个人课程已创建，可以开始整理材料了");
  } catch (error) {
    showError(error, "个人课程创建失败");
  } finally {
    loading.value = false;
  }
}

async function uploadText() {
  if (!materialText.value.trim()) return ElMessage.warning("请先输入材料内容");
  loading.value = true;
  try {
    const content_base64 = btoa(
      unescape(encodeURIComponent(materialText.value)),
    );
    await invoke("student_document_upload", {
      file_name: `${materialName.value.trim() || "文本材料"}.txt`,
      mime_type: "text/plain",
      content_base64,
    });
    materialText.value = "";
    await loadCourseData();
    ElMessage.success("文本材料已保存并解析");
  } catch (error) {
    showError(error, "文本材料保存失败");
  } finally {
    loading.value = false;
  }
}

async function uploadDocument() {
  if (!documentFile.value) return ElMessage.warning("请选择 PDF 或 Word 文件");
  if (!auth.user || !courseId.value)
    return ElMessage.warning("请先选择个人课程");
  uploadState.value = { stage: "uploading", progress: 0, message: "" };
  try {
    const file = documentFile.value;
    const form = new FormData();
    form.append("course_id", courseId.value);
    form.append("user_id", auth.user.user_id);
    form.append("role", auth.user.role);
    form.append("file", file, file.name);
    await api.post("/documents/upload", form, {
      timeout: 0,
      onUploadProgress: (event) => {
        const progress = event.total ? (event.loaded / event.total) * 100 : 0;
        uploadState.value =
          progress >= 100
            ? { stage: "processing", progress: 100, message: "" }
            : { stage: "uploading", progress, message: "" };
      },
    });
    uploadState.value = { stage: "success", progress: 100, message: "" };
    documentFile.value = null;
    await loadCourseData();
    ElMessage.success("文档已解析并保存");
  } catch (error: any) {
    const message =
      error?.response?.data?.detail ||
      error?.message ||
      "文档处理失败，可以重试";
    uploadState.value = { stage: "error", progress: 0, message };
    showError(error, "文档上传失败");
  }
}

async function extractImage() {
  if (!imageFile.value) return ElMessage.warning("请选择图片");
  loading.value = true;
  try {
    const file = imageFile.value;
    const result = await invoke("image_text_extract", {
      file_name: file.name,
      mime_type: file.type,
      content_base64: await fileToBase64(file),
    });
    await loadCourseData();
    ElMessage.success(
      `图片文字已提取并保存，共 ${String(result?.extracted_text || "").length} 字`,
    );
  } catch (error) {
    showError(error, "图片文字提取失败");
  } finally {
    loading.value = false;
  }
}

async function deleteDocument(documentId: string) {
  try {
    await ElMessageBox.confirm(
      "删除后将同时移除这份材料的检索内容，确定继续吗？",
      "删除课程材料",
      {
        type: "warning",
        confirmButtonText: "删除材料",
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    await invoke("student_document_delete", { document_id: documentId });
    await loadCourseData();
    ElMessage.success("材料已删除");
  } catch (error) {
    showError(error, "材料删除失败");
  } finally {
    loading.value = false;
  }
}

async function deleteCourse() {
  if (
    !selectedCourse.value ||
    selectedCourse.value.course_type !== "personal_course"
  )
    return;
  try {
    await ElMessageBox.confirm(
      `将永久删除“${selectedCourse.value.course_name}”及其学习记录，且无法恢复。`,
      "删除个人课程",
      {
        type: "warning",
        confirmButtonText: "永久删除课程",
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    await invoke("personal_course_delete");
    courseId.value = "";
    await loadCourses();
    ElMessage.success("个人课程已删除");
  } catch (error) {
    showError(error, "个人课程删除失败");
  } finally {
    loading.value = false;
  }
}

async function buildBlocks() {
  if (!documents.value.length) return ElMessage.warning("请先导入课程材料");
  loading.value = true;
  try {
    const result = await invoke("knowledge_blocks_build", {
      document_id: documents.value[0].document_id,
    });
    await loadCourseData();
    ElMessage.success(`已准备 ${(result || []).length} 张知识卡片，已有卡片会自动复用`);
  } catch (error) {
    showError(error, "知识卡片生成失败");
  } finally {
    loading.value = false;
  }
}

function openBlock(block: any) {
  editingBlock.value = block;
  editTitle.value = block.title;
  editKeywords.value = (block.keywords || []).join("、");
  editContent.value = block.content;
  splitPosition.value = undefined;
}
function closeBlock() {
  editingBlock.value = null;
}
function onBlockDialogChange(value: boolean) {
  if (!value) closeBlock();
}

async function openAiSplit(block: any) {
  aiSplitTargetId.value = block.block_id;
  aiSplitLoading.value = true;
  try {
    aiSplitPreview.value = await invoke("knowledge_block_ai_split", {
      block_id: block.block_id,
    });
    aiSplitDialog.value = true;
  } catch (error) {
    showError(error, "按知识点拆分失败");
  } finally {
    aiSplitLoading.value = false;
  }
}

async function applyAiSplit() {
  if (!aiSplitPreview.value?.parts?.length || !aiSplitTargetId.value)
    return ElMessage.warning("暂无可保存的语块");
  aiSplitApplying.value = true;
  try {
    blocks.value = await invoke("knowledge_block_ai_split_apply", {
      block_id: aiSplitTargetId.value,
      parts: aiSplitPreview.value.parts,
    });
    aiSplitDialog.value = false;
    aiSplitPreview.value = null;
    ElMessage.success("拆分内容已保存为独立知识卡片");
  } catch (error) {
    showError(error, "拆分内容保存失败");
  } finally {
    aiSplitApplying.value = false;
  }
}

async function saveBlock() {
  if (!editingBlock.value) return;
  loading.value = true;
  try {
    const updated = await invoke("knowledge_block_update", {
      block_id: editingBlock.value.block_id,
      title: editTitle.value,
      keywords: editKeywords.value
        .replaceAll("，", "、")
        .split("、")
        .map((x) => x.trim())
        .filter(Boolean),
      content: editContent.value,
      favorite: editingBlock.value.is_favorite,
    });
    blocks.value = blocks.value.map((item) =>
      item.block_id === updated.block_id ? updated : item,
    );
    closeBlock();
    ElMessage.success("知识卡片已保存");
  } catch (error) {
    showError(error, "知识卡片保存失败");
  } finally {
    loading.value = false;
  }
}

async function splitBlock() {
  if (!editingBlock.value || !splitPosition.value)
    return ElMessage.warning("请输入拆分位置");
  loading.value = true;
  try {
    blocks.value = await invoke("knowledge_block_split", {
      block_id: editingBlock.value.block_id,
      position: splitPosition.value,
    });
    closeBlock();
    ElMessage.success("知识卡片已拆分");
  } catch (error) {
    showError(error, "知识卡片拆分失败");
  } finally {
    loading.value = false;
  }
}

async function mergeBlock(block: any) {
  try {
    await ElMessageBox.confirm(
      "合并后两张卡片会成为一张，确定继续吗？",
      "合并知识卡片",
      { confirmButtonText: "合并卡片", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    blocks.value = await invoke("knowledge_block_merge", {
      block_id: block.block_id,
    });
    ElMessage.success("知识卡片已合并");
  } catch (error) {
    showError(error, "知识卡片合并失败");
  } finally {
    loading.value = false;
  }
}

function parseKeywords() {
  return extraKeywords.value
    .replaceAll("，", ",")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

async function generateCloze() {
  if (!trainingBlockId.value) return ElMessage.warning("请先选择训练卡片");
  loading.value = true;
  try {
    cloze.value = await invoke("cloze_generate", {
      block_id: trainingBlockId.value,
      extra_keywords: parseKeywords(),
    });
    clozeResponses.value = cloze.value.blank_count
      ? Array(cloze.value.blank_count).fill("")
      : [];
    clozeResult.value = null;
  } catch (error) {
    showError(error, "挖空生成失败");
  } finally {
    loading.value = false;
  }
}

async function submitCloze() {
  if (!trainingBlockId.value || !cloze.value) return;
  loading.value = true;
  try {
    clozeResult.value = await invoke("cloze_submit", {
      paper_id: cloze.value.paper_id,
      block_id: trainingBlockId.value,
      extra_keywords: cloze.value.keywords || cloze.value.extra_keywords || [],
      responses: clozeResponses.value,
    });
    await loadCourseData();
  } catch (error) {
    showError(error, "挖空提交失败");
  } finally {
    loading.value = false;
  }
}

function speakBlock() {
  if (!speech) return ElMessage.warning("当前浏览器不支持朗读");
  if (!trainingBlock.value) return ElMessage.warning("请先选择一张卡片");
  if (!speech.start(trainingBlock.value.content || "", speechRate.value))
    ElMessage.warning("当前卡片没有可朗读的文字");
}
watch([courseId, trainingBlockId, activeTab], () => speech?.stop());

async function evaluateRecitation() {
  if (!trainingBlockId.value || !recitedText.value.trim())
    return ElMessage.warning("请先输入或粘贴你的背诵内容");
  loading.value = true;
  try {
    const key = `${courseId.value}:${trainingBlockId.value}:${recitedText.value.trim()}`;
    if (recitationSubmission.value.key !== key) recitationSubmission.value = {key,id:crypto.randomUUID()};
    recitationResult.value = await invoke("recitation_evaluate", {
      submission_id: recitationSubmission.value.id,
      block_id: trainingBlockId.value,
      recited_text: recitedText.value.trim(),
    });
    await loadCourseData();
  } catch (error) {
    showError(error, "背诵检测失败");
  } finally {
    loading.value = false;
  }
}

async function toggleRecording() {
  if (recording.value && recorder) {
    recorder.stop();
    recorderStream?.getTracks().forEach((track) => track.stop());
    recording.value = false;
    return;
  }
  if (
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === "undefined"
  )
    return ElMessage.warning("当前浏览器不支持麦克风录音");
  try {
    recorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    recorder = new MediaRecorder(recorderStream);
    recorder.ondataavailable = (event) => {
      if (event.data.size) audioChunks.push(event.data);
    };
    recorder.onstop = () => {
      if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
      audioUrl.value = URL.createObjectURL(
        new Blob(audioChunks, { type: "audio/webm" }),
      );
    };
    recorder.start();
    recording.value = true;
  } catch (error) {
    showError(error, "无法访问麦克风，请检查浏览器权限");
  }
}

async function toggleMonitor() {
  if (monitorRunning.value) {
    await stopAudioMonitor(monitorHandle);
    monitorHandle = null;
    monitorRunning.value = false;
    return;
  }
  try {
    await ElMessageBox.confirm(
      "请先佩戴耳机并调低音量，避免扬声器产生啸叫。",
      "开启实时耳返",
      {
        confirmButtonText: "已佩戴耳机，开启",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    monitorHandle = await startAudioMonitor();
    monitorRunning.value = true;
    ElMessage.success("实时耳返已开启");
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(error?.message || "麦克风授权失败");
  }
}

function isMultiple(item: any) {
  return (
    String(item?.type || "").includes("多选") ||
    String(item?.type || "")
      .toLowerCase()
      .includes("multiple")
  );
}
function isChoice(item: any) {
  const kind = String(item?.type || "")
    .toLowerCase()
    .replace(/[ _\/-]/g, "");
  return (
    isMultiple(item) ||
    kind.includes("单选") ||
    kind.includes("判断") ||
    kind.includes("choice") ||
    kind.includes("truefalse")
  );
}
function initQuestionResponses(items: any[]) {
  return items.map((item) => (isMultiple(item) ? [] : ""));
}

async function generateMemoryQuestions() {
  loading.value = true;
  try {
    memoryQuestions.value = await invoke("memory_questions_generate", {
      count: questionCount.value,
    });
    memoryResponses.value = initQuestionResponses(memoryQuestions.value);
    memoryGrade.value = null;
  } catch (error) {
    showError(error, "练习生成失败");
  } finally {
    loading.value = false;
  }
}

async function importQuestionBank() {
  if (!questionBankFile.value) return ElMessage.warning("请选择题库文件");
  loading.value = true;
  try {
    const file = questionBankFile.value;
    memoryQuestions.value = await invoke("question_bank_import", {
      file_name: file.name,
      mime_type: file.type || "application/octet-stream",
      content_base64: await fileToBase64(file),
    });
    memoryResponses.value = initQuestionResponses(memoryQuestions.value);
    memoryGrade.value = null;
    ElMessage.success(`已载入 ${memoryQuestions.value.length} 道题`);
  } catch (error) {
    showError(error, "题库导入失败");
  } finally {
    loading.value = false;
  }
}

async function submitMemoryQuestions() {
  if (!memoryQuestions.value.length) return;
  loading.value = true;
  try {
    memoryGrade.value = await invoke("memory_questions_submit", {
      questions: memoryQuestions.value,
      responses: memoryResponses.value,
    });
    await loadCourseData();
  } catch (error) {
    showError(error, "练习批改失败");
  } finally {
    loading.value = false;
  }
}

async function loadPublishedFolders() {
  loading.value = true;
  try {
    publishedFolders.value =
      (await invoke("quiz_generate", {
        source: "published_question_folders",
      })) || [];
    if (!publishedFolderId.value && publishedFolders.value.length)
      publishedFolderId.value = publishedFolders.value[0].folder_id;
  } catch (error) {
    showError(error, "已发布题库加载失败");
  } finally {
    loading.value = false;
  }
}

async function loadPublishedBank() {
  if (!publishedFolderId.value)
    return ElMessage.warning("请先选择教师发布的试卷");
  loading.value = true;
  try {
    publishedBank.value = await invoke("quiz_generate", {
      source: "published_question_bank",
      count: 100,
      folder_id: publishedFolderId.value,
    });
    publishedPaperTitle.value = publishedFolders.value.find((folder:any)=>folder.folder_id===publishedFolderId.value)?.folder_name || "课程试卷";
    publishedResponses.value = initQuestionResponses(
      publishedBank.value?.items || [],
    );
    publishedGrade.value = null;
  } catch (error) {
    showError(error, "试卷加载失败");
  } finally {
    loading.value = false;
  }
}

async function submitPublishedBank() {
  if (!publishedBank.value) return;
  publishedBank.value.submission_id ||= crypto.randomUUID();
  loading.value = true;
  try {
    publishedGrade.value = await invoke("quiz_submit", {
      submission_id: publishedBank.value.submission_id,
      version_id: publishedBank.value.version_id,
      items: publishedBank.value.items,
      responses: publishedResponses.value,
    });
    await loadCourseData();
  } catch (error) {
    showError(error, "试卷提交失败");
  } finally {
    loading.value = false;
  }
}

function downloadBase64(result: any, fallbackName: string) {
  if (!result?.content_base64) return;
  const binary = atob(result.content_base64);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const url = URL.createObjectURL(
    new Blob([bytes], { type: "application/octet-stream" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = result.file_name || fallbackName;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function exportBook(
  action: "recitation_book_export" | "wrong_question_book_export",
) {
  loading.value = true;
  try {
    const result = await invoke(action, {
      course_name: selectedCourse.value?.course_name || "课程",
    });
    downloadBase64(
      result,
      action === "recitation_book_export"
        ? "个人背诵本.docx"
        : "个人错题本.docx",
    );
  } catch (error) {
    showError(error, "导出失败");
  } finally {
    loading.value = false;
  }
}

async function exportWorkbook() {
  if (!memoryQuestions.value.length)
    return ElMessage.warning("请先生成或导入一组练习题");
  loading.value = true;
  try {
    const result = await invoke("memory_workbook_export", {
      course_name: selectedCourse.value?.course_name || "课程",
      questions: memoryQuestions.value,
    });
    downloadBase64(result, "练习册.docx");
  } catch (error) {
    showError(error, "练习册导出失败");
  } finally {
    loading.value = false;
  }
}

function updateAiStatus(settings: any) {
  aiStatus.value = settings;
}
function onAiSettingsChanged(event: Event) {
  updateAiStatus((event as CustomEvent).detail);
}
function learningQuery(course = courseId.value, view = activeTab.value) {
  const { preview, page, source, section, ...query } = route.query;
  return { ...query, course: course || undefined, view };
}
function navigateCourse() {
  if (!navigationReady) return;
  const shared = courses.value.find(item => item.course_id === courseId.value)?.course_type === "shared_course";
  void router.push({ query: learningQuery(courseId.value, normalizeStudentView(activeTab.value, shared)) });
}
watch(activeTab, tab => {
  if (!navigationReady || route.path !== '/student/courses' || loadedCourseId !== courseId.value) return;
  if (String(route.query.view || '') !== tab)
    void router.push({ query: learningQuery(courseId.value, tab) });
});
function syncSourcePreview() {
  if (!courseId.value) { sourcePreview.value = null; return; }
  const id = typeof route.query.preview === 'string' ? route.query.preview : '';
  const pageNumber = Number(route.query.page);
  sourcePreview.value = id ? {
    document_id: id,
    page_number: Number.isInteger(pageNumber) && pageNumber > 0 ? pageNumber : null,
    source_file: typeof route.query.source === 'string' ? route.query.source : '来源资料',
    section: typeof route.query.section === 'string' ? route.query.section : '',
  } : null;
}
async function openSourcePreview(source: any) {
  await router.push({ query: { ...learningQuery(), preview: source.document_id,
    page: source.page_number || undefined, source: source.source_file || undefined,
    section: source.section || undefined }, state: { studentPreviewEntry: true } });
  await nextTick();
  if (activeTab.value === 'qa' && window.matchMedia('(max-width: 1000px)').matches) {
    document.querySelector('.source-inspector')?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
}
function closeSourcePreview() {
  if (window.history.state?.studentPreviewEntry) router.back();
  else void router.replace({ query: learningQuery() });
}
watch(() => route.fullPath, async () => {
  if (!navigationReady || route.path !== '/student/courses') return;
  const requested = String(route.query.course || '');
  const available = courses.value.find(item => item.course_id === requested);
  if (!available) {
    if (courses.value.length) await router.replace({ query: learningQuery(courses.value[0].course_id, 'qa') });
    return;
  }
  courseId.value = available.course_id;
  activeTab.value = normalizeStudentView(route.query.view, available.course_type === 'shared_course');
  syncSourcePreview();
  if (loadedCourseId !== available.course_id) await courseChanged();
});
watch([activeTab, questionCount, speechRate], () => {
  if (preferencesReady && courseId.value) writeWorkspace(auth.user?.user_id || "", `student:${courseId.value}`, {
    view: activeTab.value, questionCount: questionCount.value, speechRate: speechRate.value,
  });
});
onMounted(async () => {
  window.addEventListener("student-ai-settings-changed", onAiSettingsChanged);
  activeTab.value = normalizeStudentView(route.query.view, true);
  await loadCourses();
  const initialQuery = String(route.query.course || '') === courseId.value
    ? { ...route.query, course: courseId.value || undefined, view: activeTab.value }
    : learningQuery();
  await router.replace({ query: initialQuery });
  syncSourcePreview();
  navigationReady = true;
  try {
    aiStatus.value = (await api.get("/runtime/ai-settings")).data;
  } catch {
    aiStatus.value = null;
  }
});
onUnmounted(async () => {
  window.removeEventListener("student-ai-settings-changed", onAiSettingsChanged);
  speech?.stop();
  if (recorder?.state === "recording") recorder.stop();
  recorderStream?.getTracks().forEach((track) => track.stop());
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  await stopAudioMonitor(monitorHandle);
  monitorHandle = null;
});
</script>

<template>
  <main class="content student-workspace" :aria-busy="loading">
    <el-dialog :model-value="Boolean(sourcePreview) && (activeTab !== 'qa' || sourceExpanded)" title="来源资料预览" width="min(1200px, 96vw)" destroy-on-close @update:model-value="!$event && (activeTab === 'qa' ? sourceExpanded = false : closeSourcePreview())">
      <StudentMaterialPreview v-if="sourcePreview" :key="sourcePreview.document_id + ':' + sourcePreview.page_number" :course-id="courseId" :document-id="sourcePreview.document_id" :page-number="sourcePreview.page_number" :source-name="sourcePreview.source_file" :section="sourcePreview.section" />
    </el-dialog>
    <div class="student-app-grid">
      <aside class="student-course-nav" aria-label="课程导航">
        <div class="course-nav-heading"><button class="course-nav-toggle" :aria-expanded="courseNavOpen" @click="courseNavOpen = !courseNavOpen">我的课程<el-icon><ArrowDown/></el-icon></button><el-button :icon="Plus" circle aria-label="创建个人课程" @click="showCourseCreation"/></div>
        <div class="course-nav-groups" :class="{ 'is-open': courseNavOpen }">
          <section v-for="group in courseGroups" :key="group.type" class="course-nav-group"><h2>{{ group.title }}</h2><p>{{ group.detail }}</p>
            <button v-for="course in group.items" :key="course.course_id" class="course-nav-item" :class="{ selected: courseId === course.course_id }" :aria-current="courseId === course.course_id ? 'true' : undefined" @click="chooseSidebarCourse(course.course_id)"><el-icon><Collection/></el-icon><span>{{ course.course_name }}</span></button>
            <p v-if="!group.items.length" class="course-nav-empty">{{ group.type === 'personal_course' ? '创建课程，开始整理资料' : '加入课程后在这里显示' }}</p>
          </section>
        </div>
        <div class="course-nav-art"><StudyArtwork/><span>理解，让知识彼此相连。</span></div>
      </aside>
      <div class="student-main">
        <header class="student-header"><div class="page-title"><h1>学习空间</h1><p class="muted">围绕一门课程，理解、练习，再巩固。</p></div><StudyArtwork/></header>
    <el-progress
      v-if="loading"
      :percentage="100"
      :indeterminate="true"
      :duration="1.4"
      :show-text="false"
      class="route-progress"
      aria-label="正在处理"
    />
    <el-alert
      v-if="uploadStatusText"
      :title="uploadStatusText"
      :type="
        uploadState.stage === 'error'
          ? 'error'
          : uploadState.stage === 'success'
            ? 'success'
            : 'info'
      "
      :closable="false"
      :class="['upload-status', { 'is-processing': ['uploading', 'processing'].includes(uploadState.stage) }]"
    />
    <el-progress
      v-if="uploadState.stage === 'uploading'"
      :percentage="Math.round(uploadState.progress)"
      class="upload-progress"
    />
    <div
      v-if="uploadState.stage === 'error' && documentFile"
      class="upload-retry"
    >
      <el-button type="primary" plain @click="uploadDocument"
        >重试上传</el-button
      >
    </div>
    <el-card shadow="never" class="course-strip">
      <div class="course-strip-main">
        <div class="course-selector">
          <label for="student-course-select">当前课程</label
          ><el-select id="student-course-select"
            v-if="courses.length"
            v-model="courseId"
            @change="navigateCourse"
            ><el-option
              v-for="course in courses"
              :key="course.course_id"
              :label="course.course_name"
              :value="course.course_id" /></el-select
          ><span v-if="selectedCourse" class="muted">{{
            selectedCourse.description ||
            (selectedCourse.course_type === "personal_course"
              ? "个人课程，资料只属于你。"
              : "教师共享课程，学生可以使用资料但不能修改源文件。")
          }}</span
          ><span v-else class="muted"
            >还没有课程，可以在下方创建个人课程。</span
          >
        </div>
        <el-tag v-if="aiStatus" :type="aiStatus.configured ? 'success' : 'warning'" effect="plain">
          {{ aiStatus.mode === 'mock' || aiStatus.provider === 'mock' ? '离线演示模式' : aiStatus.configured ? '学习服务已就绪' : '学习服务待配置' }}
        </el-tag>
        <el-button text @click="loadCourses">刷新</el-button>
      </div>
    </el-card>

    <el-empty
      v-if="!courses.length"
      description="暂无已授权课程，请联系任课教师或创建个人课程"
    />
    <el-card v-if="!courseId" shadow="never" class="empty-course-card personal-course-card"
      ><template #header><b>先创建一个属于自己的学习空间</b></template>
      <p class="muted">
        个人课程适合整理教材、讲义或自己的复习材料；内容只对你可见。
      </p>
      <div class="student-two-column">
        <div class="form-field"><label for="personal-newCourseName-1">课程名称</label><el-input id="personal-newCourseName-1"
          v-model="newCourseName"
          placeholder="课程名称，例如：细胞生物学背诵"
        /></div><div class="form-field"><label for="personal-newCourseDescription-2">课程说明（可选）</label><el-input id="personal-newCourseDescription-2"
          v-model="newCourseDescription"
          placeholder="课程说明（可选）"
        /></div>
      </div>
      <el-button
        type="primary"
        class="form-button"
        @click="createPersonalCourse"
        >创建个人课程</el-button
      ></el-card
    >
    <div v-if="courseId" ref="learningWorkspace" class="learning-workspace" tabindex="-1" aria-label="课程学习工作区">
    <el-tabs
      v-study-motion="`${courseId}:${activeTab}`"
      v-model="activeTab"
      class="student-workspace-tabs"
      stretch
    >
      <el-tab-pane name="qa" label="学习问答">
        <div class="student-grid">
          <div class="learning-column">
            <el-card shadow="never" class="qa-panel"
              ><template #header
                ><div class="card-header">
                  <b>引导式答疑</b><span class="muted">只引用当前课程资料</span>
                </div></template
              ><div v-show="!session || session.completed" class="qa-composer"><label for="student-question">输入问题</label><el-select
                v-if="materialPartitions.length"
                v-model="retrievalMaterial"
                :disabled="!!session && !session.completed"
                placeholder="选择答疑资料范围"
                ><el-option label="全部已发布资料" value="all" /><el-option
                  v-for="item in materialPartitions"
                  :key="item.material_type"
                  :label="item.label || item.material_type"
                  :value="item.material_type" /></el-select
              ><el-input id="student-question"
                v-model="question"
                type="textarea"
                :rows="3"
                maxlength="500"
                show-word-limit
                placeholder="输入一个与当前课程有关的问题"
                :disabled="!!session && !session.completed"
              /><el-button
                v-if="!session || session.completed"
                type="primary"
                class="form-button"
                @click="startGuidance"
                >开始思考</el-button
              >
              </div>
              <div v-if="!messages.length" class="qa-welcome"><el-icon><Reading/></el-icon><h2>从一个问题开始</h2><p>围绕当前课程提问，跟随资料线索形成自己的答案。</p><span>回答依据会出现在右侧，方便随时核对原文。</span></div>
              <div v-if="messages.length" class="dialogue">
                <article
                  v-for="(message, index) in messages"
                  :key="index"
                  :class="['dialogue-row', message.role]"
                >
                  <strong>{{
                    message.role === "student" ? "我" : "课程助教"
                  }}</strong>
                  <p>{{ message.content }}</p>
                  <div v-if="message.role === 'assistant' && sourceLocations(message.sources).length" class="source-jumps">
                    <div v-for="(source, sourceIndex) in sourceLocations(message.sources)" :key="sourceIndex" class="source-jump">
                      <span>{{ source.source_file }} · {{ source.section }}<template v-if="source.page_number"> · 第 {{ source.page_number }} 页</template></span>
                      <el-button size="small" type="primary" plain @click="openSourcePreview(source)">{{ source.page_number ? '查看第 ' + source.page_number + ' 页' : '查看原文' }}</el-button>
                    </div>
                  </div>
                </article>
              </div>
              <template v-if="session && !session.completed"
                ><el-input
                  v-model="studentReply"
                  type="textarea"
                  :rows="2"
                  maxlength="1000"
                  show-word-limit
                  placeholder="写下你目前的判断或卡住的地方"
                />
                <div class="guided-actions">
                  <el-button type="primary" @click="guidedTurn('respond')"
                    >提交想法</el-button
                  ><el-button @click="guidedTurn('hint')">给一点提示</el-button
                  ><el-button
                    :disabled="!session.can_reveal"
                    @click="guidedTurn('reveal')"
                    >查看课程答案</el-button
                  ><el-button text @click="guidedTurn('end')"
                    >结束本题</el-button
                  >
                </div>
                <p v-if="!session.can_reveal" class="muted small">
                  完成两次自己的思考后，才可查看课程答案。
                </p></template
              ><template v-if="session?.completed"
                ><el-alert
                  v-if="session.refused"
                  type="warning"
                  :closable="false"
                  title="当前课程资料不足，本题未生成答案"
                />
                <div v-if="session.sources?.length" class="evidence-list">
                  <h3>回答依据</h3>
                  <el-collapse
                    ><el-collapse-item
                      v-for="(source, index) in session.sources"
                      :key="index"
                      :title="`${source.source_file} · ${source.section}`"
                      ><p>{{ source.text }}</p>
                      <small
                        >相关度：{{ source.score }}</small
                      ></el-collapse-item
                    ></el-collapse
                  >
                </div>
                <el-button
                  v-if="session.question_id && !session.refused"
                  type="primary"
                  class="form-button"
                  @click="generateQuiz"
                  >用这道题生成练习</el-button
                ></template
              ></el-card
            ><el-card v-if="quiz" shadow="never" class="practice-card"
              ><template #header><b>巩固练习</b></template>
              <section
                v-for="(item, index) in quiz.items"
                :key="index"
                class="practice-item"
              >
                <p>
                  <b>{{ Number(index) + 1 }}.</b> {{ item.question }}
                </p>
                <el-radio-group v-model="responses[Number(index)]"
                  ><el-radio
                    v-for="option in item.options"
                    :key="option"
                    :value="option"
                    >{{ option }}</el-radio
                  ></el-radio-group
                >
              </section>
              <el-button type="primary" @click="submitQuiz">提交练习</el-button
              ><el-result
                v-if="grade"
                :icon="grade.score >= 60 ? 'success' : 'warning'"
                :title="`${grade.score} 分`"
                :sub-title="`答对 ${grade.correct_count} / ${grade.total} 题`"
            /></el-card>
          </div>
          <aside class="profile-column">
            <section class="source-inspector" aria-label="来源内容预览">
              <div class="source-inspector-heading"><h2>来源内容预览</h2><div v-if="sourcePreview"><el-button :icon="FullScreen" text aria-label="放大来源预览" @click="sourceExpanded = true"/><el-button :icon="Close" text aria-label="关闭来源预览" @click="closeSourcePreview"/></div></div>
              <Transition name="source-slide" mode="out-in">
                <StudentMaterialPreview compact v-if="sourcePreview && !sourceExpanded" :key="courseId + ':' + sourcePreview.document_id + ':' + sourcePreview.page_number" :course-id="courseId" :document-id="sourcePreview.document_id" :page-number="sourcePreview.page_number" :source-name="sourcePreview.source_file" :section="sourcePreview.section"/>
                <div v-else-if="sourcePreview" class="source-placeholder"><p>正在放大查看来源</p></div>
                <div v-else-if="recentSources.length" key="references" class="source-reference-list"><button v-for="(source, i) in recentSources" :key="i" class="source-reference" @click="openSourcePreview(source)"><el-icon><Document/></el-icon><span><strong>{{ source.source_file }}</strong><small>{{ source.section }}<template v-if="source.page_number"> · 第 {{ source.page_number }} 页</template></small></span><span>查看</span></button></div>
                <div v-else key="empty" class="source-placeholder"><el-icon><Document/></el-icon><h3>让每个回答有据可查</h3><p>提问后，点击回答中的来源，即可在这里核对课程原文。</p><span>仅检索当前课程的可用资料</span></div>
              </Transition>
            </section>
            <el-card shadow="never"
              ><template #header
                ><div class="card-header">
                  <b>接下来复习什么</b
                  ><el-button text @click="activeTab = 'profile'"
                    >查看全部</el-button
                  >
                </div></template
              >
              <p class="muted">数据仅来自你在当前课程的问答和练习。</p>
              <el-empty
                v-if="!profile?.weak_points?.length"
                description="完成练习后生成"
                :image-size="72"
              />
              <ExpandableList :items="profile?.weak_points || []" label="复习知识点" :limit="5" :reset-key="courseId"><template #default="{items:visibleItems}"><div
                v-for="point in visibleItems"
                :key="point.knowledge_point"
                class="weak-point"
              >
                <span>{{ point.knowledge_point }}</span
                ><el-tag type="warning">{{ point.level }}</el-tag>
              </div></template></ExpandableList></el-card
            >
          </aside>
        </div>
      </el-tab-pane>
      <el-tab-pane name="materials" label="课程与材料">
        <div class="materials-workspace" :class="{ 'has-preview': selectedCourse?.course_type === 'shared_course' }">
        <el-card v-if="selectedCourse" shadow="never" class="visible-materials"
          ><template #header
            ><div class="card-header">
              <b>当前可见材料</b
              ><el-button text @click="loadCourseData">刷新</el-button>
            </div></template
          ><el-empty
            v-if="!documents.length"
            description="还没有课程材料"
            :image-size="72"
          />
          <div v-if="selectedCourse.course_type === 'shared_course' && documents.length" class="material-tree-wrap">
            <el-tree
              :data="sharedMaterialTree"
              node-key="id"
              default-expand-all
              highlight-current
              :current-node-key="previewTreeNodeId"
              :props="materialTreeProps"
              empty-text="暂无教师发布资料"
              @node-click="selectMaterialTreeNode"
            >
              <template #default="{ data }">
                <div class="material-tree-node" :class="`is-${data.kind}`">
                  <el-icon class="material-tree-icon"><Document v-if="data.kind === 'document'" /><Collection v-else /></el-icon>
                  <span class="material-tree-label">{{ data.label }}</span>
                  <small v-if="data.kind !== 'document'">{{ data.count }}</small>
                </div>
              </template>
            </el-tree>
          </div>
          <div
            v-else
            v-for="document in documents"
            :key="document.document_id"
            class="document-row"
          >
            <div>
              <strong>{{ document.original_name }}</strong>
              <p class="document-preview">
                {{ document.text_preview || "暂无文字预览" }}
              </p>
            </div>
            <el-button
              type="danger"
              text
              @click="deleteDocument(document.document_id)"
              >删除</el-button
            >
          </div></el-card
        >
          <StudentMaterialPreview
            v-if="selectedCourse?.course_type === 'shared_course' && activeTab === 'materials'"
            :key="courseId"
            v-model:selected-document-id="previewDocumentId"
            :course-id="courseId"
          />
        </div>
        <div class="student-two-column">
          <el-card shadow="never" class="personal-course-card"
            ><template #header><b>创建个人课程</b></template>
            <div class="personal-course-form">
              <div class="form-field">
                <label for="personal-newCourseName-3">课程名称</label>
                <el-input id="personal-newCourseName-3" v-model="newCourseName" placeholder="例如：细胞生物学背诵" />
              </div>
              <div class="form-field">
                <label for="personal-newCourseDescription-4">课程说明（可选）</label>
                <el-input id="personal-newCourseDescription-4" v-model="newCourseDescription" type="textarea" :rows="3" class="stack-input" placeholder="课程说明（可选）" />
              </div>
              <el-button type="primary" @click="createPersonalCourse">创建并开始整理</el-button>
            </div>
            <el-divider
              v-if="selectedCourse?.course_type === 'personal_course'"
            /><el-button
              v-if="selectedCourse?.course_type === 'personal_course'"
              type="danger"
              plain
              @click="deleteCourse"
              >删除当前个人课程</el-button
            ></el-card
          ><el-card shadow="never"
            ><template #header
              ><div class="card-header">
                <b>材料整理</b
                ><el-tag
                  v-if="selectedCourse?.course_type === 'shared_course'"
                  type="info"
                  >教师共享，只读</el-tag
                >
              </div></template
            ><el-alert
              v-if="selectedCourse?.course_type === 'shared_course'"
              title="这是教师共享课程，学生可以使用资料，但不能修改源文件。"
              type="info"
              :closable="false"
            /><template v-else
              ><h3 class="subheading">文本输入</h3>
              <el-input
                v-model="materialName"
                placeholder="材料名称"
              /><el-input
                v-model="materialText"
                type="textarea"
                :rows="6"
                class="stack-input"
                placeholder="粘贴或手动输入学习材料"
              /><el-button type="primary" @click="uploadText"
                >保存为课程材料</el-button
              ><el-divider />
              <h3 class="subheading">PDF / Word</h3>
              <label class="file-picker"
                ><input
                  type="file"
                  accept=".pdf,.docx"
                  @change="chooseFile($event, 'document')"
                />{{ documentFile?.name || "选择文件" }}</label
              ><el-button
                class="file-action"
                :disabled="!documentFile"
                @click="uploadDocument"
                >解析文档</el-button
              ><el-divider />
              <h3 class="subheading">图片文字提取</h3>
              <label class="file-picker"
                ><input
                  type="file"
                  accept=".png,.jpg,.jpeg,.webp"
                  @change="chooseFile($event, 'image')"
                />{{ imageFile?.name || "选择图片" }}</label
              ><el-button
                class="file-action"
                :disabled="!imageFile"
                @click="extractImage"
                >识别并保存文字</el-button
              ></template
            ></el-card
          >
        </div>
</el-tab-pane
      >
      <el-tab-pane name="blocks" label="知识卡片"
        ><el-card shadow="never"
          ><template #header
            ><div class="card-header">
              <div>
                <b>把材料整理成可复习的卡片</b>
                <p class="muted small">
                  根据当前课程资料整理知识卡片，生成后可手动调整。
                </p>
              </div>
              <div class="block-header-actions">
                <el-button
                  v-if="selectedCourse.course_type === 'shared_course'"
                  plain
                  @click="openPublishedKnowledgeDialog"
                  >导入教师已发布知识</el-button
                ><el-button
                  v-if="selectedCourse.course_type === 'personal_course'"
                  type="primary"
                  :disabled="!documents.length"
                  @click="buildBlocks"
                  >整理知识卡片</el-button
                >
              </div>
            </div></template
          ><el-empty
            v-if="!blocks.length"
            description="请先导入材料并生成知识卡片"
          />
          <div v-else>
            <div class="card-view-switch"><span>复习与整理</span><el-radio-group v-model="cardView" aria-label="知识卡片显示方式"><el-radio-button value="deck">逐张复习</el-radio-button><el-radio-button value="list">列表编辑</el-radio-button></el-radio-group></div>
            <StudyCardDeck v-if="cardView === 'deck'" :cards="blocks" :course-id="courseId"/>
            <div v-else class="knowledge-card-grid">
            <el-card
              v-for="(block, index) in blocks"
              :key="block.block_id"
              shadow="never"
              class="knowledge-card"
              :style="{ '--motion-index': index }"
              ><div class="knowledge-card-heading">
                <div class="knowledge-card-title">
                  <b>{{ block.title }}</b>
                  <el-tag v-if="block.is_favorite" type="warning">重点</el-tag>
                </div>
              <div class="knowledge-card-tools">
                <el-button size="small" @click="openBlock(block)"
                  >编辑</el-button
                ><el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="aiSplitLoading && aiSplitTargetId === block.block_id"
                  @click="openAiSplit(block)"
                  >按知识点拆分</el-button
                ><el-button size="small" @click="mergeBlock(block)"
                  >合并下一张</el-button
                >
              </div>
              </div>
              <KnowledgeMarkdown :content="block.content" />
              <div class="keyword-list">
                <el-tag
                  v-for="keyword in block.keywords || []"
                  :key="keyword"
                  effect="plain"
                  >{{ keyword }}</el-tag
                >
              </div></el-card
            >
          </div></div></el-card
        ><el-dialog
          v-model="publishedKnowledgeDialog"
          title="导入教师已发布知识点"
          width="min(860px, 94vw)"
          ><p class="muted small">
            这里只显示当前共享课程最新发布版本中的知识点。导入时优先按章节号和知识点编号拆分，每个知识点保留对应解释、下级条目和公式。
          </p>
          <el-alert
            v-if="publishedKnowledge?.version"
            type="info"
            :closable="false"
            :title="`教师发布版本 v${publishedKnowledge.version.version_number} · 可选 ${publishedKnowledge.items?.length || 0} 个知识点`"
          />
          <el-empty
            v-if="!publishedKnowledge?.items?.length"
            description="教师暂未发布可导入的知识点"
          />
          <el-checkbox-group
            v-else
            v-model="selectedPublishedNodeIds"
            class="published-knowledge-list"
          >
            <article
              v-for="item in publishedKnowledge.items"
              :key="item.node_id"
              class="published-knowledge-item"
            >
              <div class="published-knowledge-heading">
                <el-checkbox
                  :label="String(item.node_id)"
                  :disabled="item.imported"
                  >{{ item.title }}</el-checkbox
                >
                <el-tag v-if="item.imported" type="success" size="small"
                  >已导入</el-tag
                >
                <el-tag v-if="item.update_available" type="warning" size="small"
                  >教师已更新，个人卡片保留原修改</el-tag
                >
              </div>
              <KnowledgeMarkdown :content="item.content" />
              <small class="muted"
                >{{ item.original_name || "教师课程资料" }} ·
                {{ item.material_type || "课程知识" }}</small
              >
            </article>
          </el-checkbox-group>
          <template #footer
            ><el-button @click="publishedKnowledgeDialog = false"
              >取消</el-button
            ><el-button
              type="primary"
              :loading="importingPublished"
              :disabled="!selectedPublishedNodeIds.length"
              @click="importPublishedKnowledge"
              >导入选中知识点</el-button
            ></template
        ></el-dialog
        ><el-dialog
          v-model="aiSplitDialog"
          title="预览拆分结果"
          width="min(820px, 94vw)"
        >
          <el-alert
            type="info"
            :closable="false"
            title="只处理你自己的知识卡片"
            description="优先按章节号和知识点编号拆分，保留对应解释与下级条目；无清晰编号时按内容含义拆分。确认保存后生成新卡片。"
          />
          <div v-if="aiSplitPreview" class="ai-split-preview">
            <div class="ai-split-source">
              <span class="muted small">原知识点</span>
              <b>{{ aiSplitPreview.source_title }}</b>
              <KnowledgeMarkdown :content="aiSplitPreview.source_content" />
            </div>
            <div class="ai-split-parts">
              <div class="ai-split-heading">
                <b>分析结果（{{ aiSplitPreview.parts.length }} 个语块）</b>
                <span class="muted small">可在保存前调整标题和内容</span>
              </div>
              <article
                v-for="(part, index) in aiSplitPreview.parts"
                :key="index"
                class="ai-split-part"
              >
                <div class="ai-split-part-title">
                  <el-tag size="small">语块 {{ Number(index) + 1 }}</el-tag>
                  <el-input v-model="part.title" placeholder="语块标题" />
                </div>
                <el-input
                  v-model="part.content"
                  type="textarea"
                  :rows="4"
                  placeholder="语块内容"
                />
                <div class="keyword-list">
                  <el-tag
                    v-for="keyword in part.keywords || []"
                    :key="keyword"
                    size="small"
                    effect="plain"
                    >{{ keyword }}</el-tag
                  >
                </div>
              </article>
            </div>
          </div>
          <template #footer>
            <el-button @click="aiSplitDialog = false">取消</el-button>
            <el-button type="primary" :loading="aiSplitApplying" @click="applyAiSplit"
              >确认并生成知识卡片</el-button
            >
          </template>
        </el-dialog
        ><el-dialog
          :model-value="!!editingBlock"
          title="调整知识卡片"
          width="min(720px, 92vw)"
          @update:model-value="onBlockDialogChange"
          ><el-input v-model="editTitle" placeholder="卡片标题" /><el-input
            v-model="editKeywords"
            class="stack-input"
            placeholder="关键词，用顿号分隔"
          /><el-input
            v-model="editContent"
            class="stack-input"
            type="textarea"
            :rows="8"
            placeholder="卡片内容"
          />
          <div class="stack-input">
            <b>内容预览</b>
            <KnowledgeMarkdown :content="editContent" />
          </div>
          <div class="split-row">
            <el-input-number
              v-model="splitPosition"
              :min="20"
              :max="Math.max(20, editContent.length - 20)"
              placeholder="拆分位置"
            /><el-button @click="splitBlock">按预览位置拆分</el-button>
          </div>
          <div class="split-visual">
            <div class="split-heading">
              <b>拆分位置可视化</b>
              <span>第 {{ splitAt }} / {{ editContent.length }} 个字符</span>
            </div>
            <el-slider
              v-model="splitPosition"
              :min="20"
              :max="Math.max(20, editContent.length - 20)"
              :disabled="editContent.length < 40"
              show-input
            />
            <div class="split-preview">
              <article><b>前一张卡片</b><pre>{{ splitBefore || "（暂无内容）" }}</pre></article>
              <i>拆分线</i>
              <article><b>后一张卡片</b><pre>{{ splitAfter || "（暂无内容）" }}</pre></article>
            </div>
          </div>
          <template #footer
            ><el-button @click="closeBlock">取消</el-button
            ><el-button type="primary" @click="saveBlock"
              >保存卡片</el-button
            ></template
          ></el-dialog
        ></el-tab-pane
      >
      <el-tab-pane name="training" label="训练巩固">
        <section class="training-workspace" aria-label="训练巩固工作台">
          <div class="training-context-card">
            <div class="training-context-copy">
              <div class="training-eyebrow"><span>学习训练台</span><span>ACTIVE SESSION</span></div>
              <div class="training-context-title"><b>{{ trainingBlock?.title || "选择一张知识卡片" }}</b><el-tag v-if="trainingBlock" size="small" effect="plain">第 {{ trainingIndex + 1 }} / {{ blocks.length }} 张</el-tag></div>
              <p>{{ trainingBlock ? "围绕当前知识点完成听读、复述和记忆提取。" : "先选择一张知识卡片，再开始训练。" }}</p>
            </div>
            <div class="training-context-select">
              <label for="training-card-picker">训练卡片</label>
              <el-select id="training-card-picker" v-model="trainingBlockId" placeholder="选择训练卡片">
                <el-option v-for="block in blocks" :key="block.block_id" :label="block.title" :value="block.block_id" />
              </el-select>
            </div>
          </div>

          <div v-if="trainingBlock" class="training-layout">
            <section class="training-panel recall-panel">
              <div class="training-panel-heading">
                <span class="training-step-mark">01</span>
                <div><span class="training-panel-kicker">RECALL</span><h2>记忆提取</h2><p>先从重点词回想完整知识，再核对答案。</p></div>
                <el-tag v-if="clozeResult" size="small" :type="clozeResult.score >= 60 ? 'success' : 'warning'">{{ clozeResult.score }}%</el-tag>
                <el-tag v-else size="small" type="info">待开始</el-tag>
              </div>

              <div class="training-card-context">
                <span>当前卡片</span>
                <strong>{{ trainingBlock.title }}</strong>
                <div class="training-keywords">
                  <el-tag v-for="keyword in trainingBlock.keywords || []" :key="keyword" size="small" effect="plain">{{ keyword }}</el-tag>
                  <small v-if="!trainingBlock.keywords?.length">暂无提取重点，可手动追加。</small>
                </div>
              </div>

              <div class="training-form-block">
                <div class="training-label-row"><span>追加重点词</span><small>可选 · 用逗号分隔</small></div>
                <el-input v-model="extraKeywords" placeholder="例如：索引、范式、事务" />
                <el-button type="primary" class="training-primary-action" @click="generateCloze">生成挖空</el-button>
              </div>

              <div v-if="cloze" class="cloze-panel training-result-panel">
                <div class="training-result-heading"><span>挖空练习</span><small>{{ cloze.segments?.length || 0 }} 个内容片段</small></div>
                <h3>{{ cloze.title }}</h3>
                <el-alert v-if="cloze.keyword_source === 'AI 分析重点'" type="success" :closable="false" :title="`已提取 ${cloze.keywords?.length || 0} 个重点用于挖空`" />
                <p v-if="cloze.keywords?.length" class="muted small">本次重点：{{ cloze.keywords.join("、") }}</p>
                <p class="cloze-text"><template v-for="(segment, index) in cloze.segments" :key="index"><span v-if="segment.type === 'text'">{{ segment.value }}</span><el-tag v-else type="warning">第 {{ segment.index }} 空</el-tag></template></p>
                <div class="cloze-inputs"><el-input v-for="(_, index) in clozeResponses" :key="index" v-model="clozeResponses[Number(index)]" :placeholder="`第 ${Number(index) + 1} 空`" /></div>
                <el-button type="primary" @click="submitCloze">提交并检测</el-button>
              </div>
              <div v-else class="training-empty-state"><span class="training-empty-mark">回想</span><strong>生成一组挖空，开始检验记忆</strong><p>系统会根据当前卡片的重点内容生成练习。</p></div>
              <el-result v-if="clozeResult" :icon="clozeResult.score >= 60 ? 'success' : 'warning'" :title="`正确率 ${clozeResult.score}%`" :sub-title="`答对 ${clozeResult.correct_count} / ${clozeResult.total} 空`" />
            </section>

            <section class="training-panel audio-panel">
              <div class="training-panel-heading">
                <span class="training-step-mark">02</span>
                <div><span class="training-panel-kicker">LISTEN & SPEAK</span><h2>听觉强化与跟读</h2><p>听一遍、说一遍、录一遍，把知识点变成自己的表达。</p></div>
                <span class="training-live-dot" :class="{ active: speechState !== 'idle' || recording }">{{ speechState !== 'idle' || recording ? '进行中' : '未开始' }}</span>
              </div>

              <div class="training-stage-list">
                <article class="training-stage-item">
                  <span class="training-stage-index">A</span>
                  <div class="training-stage-content">
                    <div class="training-stage-title"><strong>先听一遍</strong><small>自动切换中英文发音</small></div>
                    <el-slider v-model="speechRate" :min="0.75" :max="2" :step="0.25" :disabled="speechState !== 'idle'" aria-label="朗读速度" show-stops />
                    <div class="speech-controls">
                      <el-button type="primary" :disabled="!speech" @click="speakBlock">{{ speechState === 'idle' ? '朗读当前卡片' : '重新朗读' }}</el-button>
                      <el-button :disabled="speechState === 'idle'" @click="speechState === 'paused' ? speech?.resume() : speech?.pause()">{{ speechState === 'paused' ? '继续朗读' : '暂停朗读' }}</el-button>
                      <el-button :disabled="speechState === 'idle'" @click="speech?.stop()">停止朗读</el-button>
                      <span class="muted" role="status">{{ speechState === 'paused' ? '已暂停' : speechState === 'speaking' ? '正在朗读' : '未朗读' }} · {{ speechRate }} 倍速</span>
                    </div>
                  </div>
                </article>
                <article class="training-stage-item">
                  <span class="training-stage-index">B</span>
                  <div class="training-stage-content">
                    <div class="training-stage-title"><strong>复述检查</strong><small>用自己的话写下理解</small></div>
                    <el-input v-model="recitedText" type="textarea" :rows="5" placeholder="输入你的复述内容，检查遗漏与理解偏差" />
                    <el-button class="training-secondary-action" @click="evaluateRecitation">检测复述</el-button>
                    <el-result v-if="recitationResult" :title="`背诵评分 ${recitationResult.score}`" :sub-title="recitationResult.feedback" />
                  </div>
                </article>
                <article class="training-stage-item">
                  <span class="training-stage-index">C</span>
                  <div class="training-stage-content">
                    <div class="training-stage-title"><strong>跟读记录</strong><small>录音仅保存在当前浏览器</small></div>
                    <el-button plain @click="toggleRecording">{{ recording ? "停止录音" : "录一段跟读" }}</el-button>
                    <audio v-if="audioUrl" :src="audioUrl" controls class="audio-player" />
                  </div>
                </article>
              </div>
            </section>
          </div>
          <el-empty v-else description="请先在知识卡片中生成至少一张卡片" />
        </section>
      </el-tab-pane>
      <el-tab-pane name="practice" label="作答与测验"
        ><el-card
          v-if="selectedCourse?.course_type === 'shared_course'"
          shadow="never"
          ><template #header
            ><div class="card-header">
              <b>教师审核题库</b
              ><el-button @click="loadPublishedFolders"
                >刷新已发布试卷</el-button
              >
            </div></template
          >
          <p class="muted">这里只展示教师审核并发布的题目。</p>
          <el-select
            v-model="publishedFolderId"
            placeholder="选择教师发布的试卷"
            ><el-option
              v-for="folder in publishedFolders"
              :key="folder.folder_id"
              :label="`${folder.folder_name}（${folder.item_count} 题）`"
              :value="folder.folder_id" /></el-select
          ><el-button
            type="primary"
            class="form-button"
            :disabled="!publishedFolderId"
            @click="loadPublishedBank"
            >载入整份任务</el-button
          >
          <PaperWorkspace v-if="publishedBank?.items?.length" :key="publishedBank.version_id" :title="publishedPaperTitle"
            :subtitle="`题库版本 ${publishedBank.version_number} · 共 ${publishedBank.items.length} 题`" :items="publishedBank.items"
            :answered="publishedBank.items.map((_:any,index:number)=>Array.isArray(publishedResponses[index]) ? publishedResponses[index].length>0 : publishedResponses[index]!=null && String(publishedResponses[index]).trim()!=='')" :disabled="loading">
            <template #answer="{item,index}">
              <el-checkbox-group v-if="isMultiple(item)" v-model="publishedResponses[index]" :disabled="loading"><el-checkbox v-for="option in questionOptions(item)" :key="option.key" :value="option.key">{{ option.text }}</el-checkbox></el-checkbox-group>
              <el-radio-group v-else-if="isChoice(item)" v-model="publishedResponses[index]" :disabled="loading"><el-radio v-for="option in questionOptions(item)" :key="option.key" :value="option.key">{{ option.text }}</el-radio></el-radio-group>
              <el-input v-else v-model="publishedResponses[index]" type="textarea" :rows="4" placeholder="请输入答案" :aria-label="`第 ${index+1} 题答案`" :disabled="loading" />
            </template>
            <template #submit><el-button type="primary" :loading="loading" @click="submitPublishedBank">提交本次答案</el-button></template>
            <template #result><el-result v-if="publishedGrade" icon="success" :title="`本次正确率 ${publishedGrade.accuracy}%`" /></template>
          </PaperWorkspace>
          </el-card
        ><el-card shadow="never" class="nested-card"
          ><template #header
            ><div class="card-header">
              <div>
                <b>专项练习</b>
                <p class="muted small">
                  根据知识卡片生成，也可以导入 PDF、Word、TXT 或 XLSX 题库。
                </p>
              </div>
              <el-button @click="exportWorkbook">导出 Word 练习册</el-button>
            </div></template
          >
          <div class="practice-toolbar">
            <el-input-number
              v-model="questionCount"
              :min="3"
              :max="12"
            /><el-button
              class="practice-source-button"
              type="primary"
              :disabled="!blocks.length"
              @click="generateMemoryQuestions"
              >根据知识卡片生成</el-button
            ><label
              class="file-picker practice-source-button"
              :title="questionBankFile?.name || '选择题库文件'"
              ><input
                type="file"
                accept=".pdf,.docx,.txt,.xlsx,.xls"
                @change="chooseFile($event, 'questionBank')"
              />{{ questionBankFile?.name || "选择题库文件" }}</label
            ><el-button
              :disabled="!questionBankFile"
              @click="importQuestionBank"
              >解析并载入题库</el-button
            >
          </div>
          <el-empty
            v-if="!memoryQuestions.length"
            description="生成或导入一组练习后，在这里作答" />
          <PaperWorkspace v-else title="专项练习" subtitle="完成后统一提交，查看本次练习反馈。" :items="memoryQuestions"
            :answered="memoryQuestions.map((_:any,index:number)=>Array.isArray(memoryResponses[index]) ? memoryResponses[index].length>0 : memoryResponses[index]!=null && String(memoryResponses[index]).trim()!=='')" :disabled="loading">
            <template #answer="{item,index}">
              <el-checkbox-group v-if="isMultiple(item)" v-model="memoryResponses[index]" :disabled="loading"><el-checkbox v-for="option in questionOptions(item)" :key="option.key" :value="option.key">{{ option.text }}</el-checkbox></el-checkbox-group>
              <el-radio-group v-else-if="isChoice(item)" v-model="memoryResponses[index]" :disabled="loading"><el-radio v-for="option in questionOptions(item)" :key="option.key" :value="option.key">{{ option.text }}</el-radio></el-radio-group>
              <el-input v-else v-model="memoryResponses[index]" type="textarea" :rows="4" placeholder="请输入简答内容" :aria-label="`第 ${index+1} 题答案`" :disabled="loading" />
            </template>
            <template #submit><el-button type="primary" :loading="loading" @click="submitMemoryQuestions">提交并查看批改</el-button></template>
            <template #result><el-result v-if="memoryGrade" icon="success" :title="`本次正确率 ${memoryGrade.score}%`" :sub-title="memoryGrade.summary" /></template>
          </PaperWorkspace></el-card
      ></el-tab-pane>
      <el-tab-pane name="profile" label="我的学习"
        ><el-card shadow="never"
          ><template #header
            ><div class="card-header">
              <b>我的学习画像</b
              ><el-button text @click="loadCourseData">刷新</el-button>
            </div></template
          >
          <div class="profile-metrics profile-metrics-wide">
            <span
              >我的课程
              <b>{{ dashboard?.course_count || courses.length }}</b></span
            ><span
              >课程资料
              <b>{{ dashboard?.document_count || documents.length }}</b></span
            ><span
              >知识块 <b>{{ dashboard?.block_count || blocks.length }}</b></span
            ><span
              >背诵平均 <b>{{ dashboard?.memory_attempts?.length ? `${dashboard.memory_average}%` : '暂无作答' }}</b></span
            ><span
              >练习平均 <b>{{ dashboard?.practice_attempts?.length ? `${dashboard.practice_average}%` : '暂无作答' }}</b></span
            ><span
              >试卷平均 <b>{{ dashboard?.published_attempts?.length ? `${dashboard.published_average}%` : '暂无作答' }}</b></span
            >
          </div>
          <div class="student-two-column">
            <div>
              <h3>薄弱知识统计</h3>
              <el-empty
                v-if="!dashboard?.weak_points?.length"
                description="完成一次挖空或练习后生成"
                :image-size="72"
              />
              <ExpandableList :items="dashboard?.weak_points || []" label="薄弱知识点" :limit="5" :reset-key="courseId"><template #default="{items:visibleItems}"><div
                v-for="point in visibleItems"
                :key="point.point"
                class="weak-point"
              >
                <span>{{ point.point }}</span
                ><el-tag type="warning">{{ point.count }} 次</el-tag>
              </div></template></ExpandableList>
            </div>
            <div class="learning-history">
              <div class="history-heading">
                <div>
                  <h3>最近学习成绩</h3>
                  <p>按完成时间显示最近 10 次学习表现</p>
                </div>
                <span v-if="recentLearningAttempts.length" class="history-count"
                  >{{ recentLearningAttempts.length }} 条记录</span
                >
              </div>
              <el-empty
                v-if="!recentLearningAttempts.length"
                description="暂无学习记录"
                :image-size="72"
              />
              <div v-else class="history-list">
                <ExpandableList :items="recentLearningAttempts" label="学习成绩" :limit="5" :reset-key="courseId"><template #default="{items:visibleItems}"><article
                  v-for="attempt in visibleItems"
                  :key="attempt.source + '-' + attempt.attempt_id"
                  class="history-row"
                >
                  <div class="history-row-info">
                    <span class="history-kind" :class="attempt.source"
                      >{{ learningAttemptLabel(attempt) }}</span
                    >
                    <strong>{{ learningAttemptTitle(attempt) }}</strong>
                  </div>
                  <div class="history-score" :class="learningScoreTone(attempt.score)">
                    <strong>{{ attempt.score ?? 0 }}</strong>
                    <small>得分</small>
                  </div>
                  <time>{{ formatLearningDate(attempt.created_at) }}</time>
                </article></template></ExpandableList>
              </div>
            </div>
          </div></el-card
        ><el-card shadow="never" class="nested-card notebook-panel">
          <template #header>
            <div class="notebook-heading">
              <h2>我的背诵本与错题本</h2>
              <div class="export-actions">
                <el-button @click="exportBook('recitation_book_export')">导出 Word 背诵本</el-button>
                <el-button @click="exportBook('wrong_question_book_export')">导出 Word 错题本</el-button>
              </div>
            </div>
          </template>
          <el-empty v-if="!dashboard?.recitation_book?.length && !dashboard?.wrong_question_book?.length" description="暂无错背或错题记录" :image-size="80" />
          <section v-if="dashboard?.recitation_book?.length" class="notebook-section" aria-label="背诵记录">
            <h3 class="notebook-section-title">背诵记录 <span>{{ dashboard.recitation_book.length }} 条</span></h3>
            <ExpandableList :items="dashboard.recitation_book" label="背诵记录" :limit="5" :reset-key="courseId"><template #default="{items:visibleItems}">
              <article v-for="item in visibleItems" :key="`recite-${item.attempt_id}`" class="notebook-record">
                <div class="notebook-meta"><span>{{ learningModeLabel(item.mode) }}</span><span>得分 <strong>{{ item.score }}%</strong></span></div>
                <h4>{{ item.title || "知识块" }}</h4>
                <p v-if="item.feedback" class="notebook-feedback">{{ item.feedback }}</p>
              </article>
            </template></ExpandableList>
          </section>
          <section v-if="dashboard?.wrong_question_book?.length" class="notebook-section" aria-label="错题记录">
            <h3 class="notebook-section-title">错题记录 <span>{{ dashboard.wrong_question_book.length }} 条</span></h3>
            <ExpandableList :items="dashboard.wrong_question_book" label="错题记录" :limit="5" :reset-key="courseId"><template #default="{items:visibleItems}">
              <article v-for="item in visibleItems" :key="`wrong-${item.created_at}-${item.question}`" class="notebook-record">
                <div class="notebook-meta"><span>{{ questionTypeLabel(item.type) }}</span></div>
                <h4>{{ item.question }}</h4>
                <p class="notebook-answer"><span>正确答案</span><strong>{{ questionAnswerLabel(item.correct_answer, item.type) }}</strong></p>
                <p v-if="item.feedback" class="notebook-feedback">{{ item.feedback }}</p>
              </article>
            </template></ExpandableList>
          </section>
        </el-card
        ></el-tab-pane
      >
      <el-tab-pane
        v-if="selectedCourse?.course_type === 'shared_course'"
        name="graph"
        label="课程知识图谱"
      >
        <el-card shadow="never">
          <template #header
            ><div class="card-header">
              <div>
                <b>课程知识图谱</b>
                <p class="muted small">
                  查看教师最新发布的知识结构；草稿和待审核关系不会在这里出现。
                </p>
              </div>
              <div class="graph-tools">
                <el-input
                  v-model="graphSearch"
                  clearable
                  placeholder="搜索知识点"
                /><el-radio-group v-model="graphLayout" size="small"
                  ><el-radio-button value="force">力导向</el-radio-button
                  ><el-radio-button value="circular"
                    >环形</el-radio-button
                  ></el-radio-group
                ><el-button @click="loadPublishedGraph">刷新</el-button>
              </div>
            </div></template
          >
          <el-empty
            v-if="!publishedGraph?.nodes?.length"
            description="教师尚未发布课程知识图谱"
          />
          <div v-else class="student-graph-layout">
            <KnowledgeGraphCanvas forest-palette
              :nodes="publishedGraph.nodes"
              :relations="publishedGraph.relations"
              :search="graphSearch"
              :layout="graphLayout"
              @select-node="graphSelectedNode = $event"
            />
            <aside class="student-graph-detail">
              <template v-if="graphSelectedNode"
                ><h3>{{ graphSelectedNode.title }}</h3>
                <div class="graph-marker-row">
                  <el-tag
                    v-for="marker in graphSelectedNode.markers || []"
                    :key="marker"
                    type="success"
                    >{{ marker }}</el-tag
                  >
                </div>
                <p>{{ graphSelectedNode.summary || "暂无摘要" }}</p>
                <small
                  >图谱版本 v{{ publishedGraph.version?.version_number }}</small
                ></template
              ><el-empty
                v-else
                description="点击节点查看摘要"
                :image-size="64"
              />
            </aside>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
    </div>
    <el-card
      v-if="courseId && activeTab === 'training'"
      shadow="never"
      class="nested-card audio-monitor-card"
    >
      <template #header
        ><div class="card-header">
          <div>
            <b>实时耳返</b>
            <p class="muted small">
              请先佩戴耳机；音频只在浏览器中播放，不会上传。
            </p>
          </div>
          <el-tag :type="monitorRunning ? 'success' : 'info'">{{
            monitorRunning ? "运行中" : "未开启"
          }}</el-tag>
        </div></template
      >
      <el-button
        :type="monitorRunning ? 'danger' : 'primary'"
        @click="toggleMonitor"
        >{{ monitorRunning ? "停止实时耳返" : "开启实时耳返" }}</el-button
      >
    </el-card>
    <StudentLearningFocus v-if="selectedCourse" :mode="activeTab" :course-name="selectedCourse.course_name"
      :documents="documents.length" :blocks="blocks.length" :preparing="startingReview" :loading="loading"
      @resume="resumeLearning" @review="startReview" @profile="activeTab = 'profile'" />
      </div>
    </div>
  </main>
</template>

<style scoped>
.notebook-panel{margin-top:24px}.notebook-panel :deep(.el-card__header){padding:22px 26px}.notebook-panel :deep(.el-card__body){padding:26px}
.notebook-heading{display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap}.notebook-heading h2{margin:0;font-size:18px;line-height:1.5;font-weight:650;letter-spacing:0;color:#253b33}.notebook-heading .export-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:0}.notebook-heading .el-button{margin:0;min-height:36px;padding:9px 15px;border-radius:8px;font-size:13px;line-height:1.3;font-weight:500;color:#36594b;border-color:#d3e0d8;background:#fafcf9}
.notebook-section + .notebook-section{margin-top:30px;padding-top:26px;border-top:1px solid #dce5df}.notebook-section-title{display:flex;align-items:baseline;gap:10px;margin:0 0 20px;font-size:15px;font-weight:600;line-height:1.5;color:#29473a}.notebook-section-title>span{font-size:12px;font-weight:400;color:#61736a;font-variant-numeric:tabular-nums}
.notebook-record{padding:0 0 20px;max-width:100%;min-width:0}.notebook-record + .notebook-record{padding-top:20px;border-top:1px solid #edf1ed}.notebook-record:last-child{padding-bottom:0}.notebook-meta{display:flex;align-items:center;flex-wrap:wrap;gap:18px;margin-bottom:8px;font-size:12px;line-height:1.5;color:#61736a}.notebook-meta strong{margin-left:4px;font-weight:550;font-variant-numeric:tabular-nums;color:#385d4c}.notebook-record h4{max-width:90ch;margin:0;font-size:15px;line-height:1.85;font-weight:550;color:#253b33;overflow-wrap:anywhere;white-space:pre-wrap}.notebook-answer{display:flex;align-items:baseline;gap:14px;margin:12px 0 0;font-size:14px;line-height:1.75}.notebook-answer>span{flex-shrink:0;color:#61736a;font-size:13px}.notebook-answer>strong{color:#294b3c;font-weight:550;overflow-wrap:anywhere;white-space:pre-wrap}.notebook-feedback{max-width:90ch;margin:10px 0 0;font-size:14px;line-height:1.8;font-weight:400;color:#56634d;overflow-wrap:anywhere;white-space:pre-wrap}
@media(prefers-reduced-motion:no-preference){.notebook-heading .el-button{transition:background-color .18s,border-color .18s,transform .18s}.notebook-heading .el-button:hover{background:#edf5f0;border-color:#94b9a8}.notebook-heading .el-button:active{transform:scale(.98)}}

.history-row + .history-row{margin-top:8px}

.materials-workspace{display:grid;gap:20px;align-items:start;margin-bottom:20px;min-width:0}
.materials-workspace.has-preview{grid-template-columns:minmax(240px,1fr) minmax(0,2fr)}
.visible-materials{min-width:0}
.visible-materials :deep(.el-card__body){max-height:75vh;overflow:auto}
.materials-workspace :deep(.student-material-preview){margin-bottom:0}
.material-tree-wrap{padding:4px 2px 8px}
.material-tree-wrap :deep(.el-tree){background:transparent;--el-tree-node-hover-bg-color:#f1f5ee;--el-tree-text-color:#294b3c}
.material-tree-node{display:flex;align-items:center;gap:8px;width:100%;min-width:0;padding:4px 6px 4px 0}
.material-tree-icon{flex:none;color:#688463}
.material-tree-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.material-tree-node.is-category .material-tree-label{font-weight:700}
.material-tree-node.is-tag .material-tree-label{color:#56634d}
.material-tree-node small{margin-left:auto;color:#718174;font-variant-numeric:tabular-nums}
@media(max-width:800px){.materials-workspace.has-preview{grid-template-columns:minmax(0,1fr)}.visible-materials :deep(.el-card__body){max-height:280px}}

.student-workspace{--study-ease:cubic-bezier(.16,1,.3,1);padding-top:28px;max-width:1440px}
.student-header{align-items:center;margin-bottom:22px;gap:20px}.student-header h1{font-size:28px;font-weight:650;margin:0 0 6px}.student-header p{font-size:13px;margin:0;color:#56634d}.student-account{gap:8px;flex-shrink:0}.account-menu :deep(span){gap:10px}.account-menu{background:transparent;border-color:#d4e1da}
.course-strip{margin-bottom:18px;border-radius:12px;background:#fff}.course-strip :deep(.el-card__body){padding:16px 20px}.course-selector{grid-template-columns:70px minmax(200px,320px) minmax(0,1fr)}.course-selector>label{font-size:13px}.course-selector>.muted{font-size:12px;color:#56634d}.course-strip-main>.el-tag{flex-shrink:0}
.learning-workspace{margin-top:26px;scroll-margin-top:24px}.learning-workspace:focus-visible{outline:2px solid #294b3c;outline-offset:6px}.student-workspace-tabs :deep(.el-tabs__header){margin-bottom:22px}.student-workspace-tabs :deep(.el-tabs__item){height:48px;font-size:14px}.student-workspace-tabs :deep(.el-tabs__active-bar){height:3px;border-radius:3px}.student-workspace-tabs :deep(.el-tabs__nav-wrap::after){height:1px;background:#dbe5de}.student-workspace-tabs :deep(.el-card__header){padding:18px 22px}.student-workspace-tabs :deep(.el-card__body){padding:22px}.student-grid{grid-template-columns:minmax(0,1fr) 280px;gap:24px}.profile-column :deep(.el-card){background:#f9fbf8}.profile-column .muted{font-size:13px;line-height:1.7;color:#56634d}
@media(prefers-reduced-motion:no-preference){.student-header{animation:workspace-arrive .45s var(--study-ease) both}.course-strip{animation:workspace-arrive .55s .06s var(--study-ease) both}.student-workspace-tabs :deep(.el-tabs__active-bar){transition:transform .28s var(--study-ease)}.dialogue-row{animation:workspace-arrive .35s var(--study-ease) both}}
@keyframes workspace-arrive{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@media(prefers-reduced-motion:reduce){.student-workspace :deep(*),.student-workspace :deep(*::before),.student-workspace :deep(*::after){animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media(max-width:1100px){.student-header{align-items:flex-start}.student-grid{grid-template-columns:minmax(0,1fr) 240px}.course-selector{grid-template-columns:70px minmax(180px,1fr)}.course-selector>.muted{grid-column:2}.student-account{flex-wrap:wrap;justify-content:flex-end}}
@media(max-width:760px){.student-grid{grid-template-columns:1fr}.student-header{display:block}.student-account{justify-content:flex-start}.course-strip-main{flex-wrap:wrap}}
.source-jumps{display:grid;gap:8px;margin-top:10px}.source-jump{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px;border:1px solid #dce8e5;border-radius:8px;background:#f5faf8}.source-jump span{min-width:0;overflow-wrap:anywhere;font-size:13px;color:#47685f}.source-jump .el-button{flex-shrink:0}
.training-workspace{display:grid;gap:16px}.training-context-card{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,360px);align-items:stretch;gap:20px;padding:22px 24px;border:1px solid #dce1d4;border-radius:14px;background:#fcfcf8;box-shadow:0 12px 28px rgba(41,75,60,.06)}.training-context-copy{display:grid;align-content:center;gap:8px;min-width:0}.training-eyebrow{display:flex;align-items:center;gap:10px;color:#72806e;font-size:10px;letter-spacing:.12em}.training-eyebrow span:last-child{color:#9ba494;font-size:9px}.training-context-title{display:flex;align-items:center;gap:10px;min-width:0}.training-context-title b{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#294b3c;font-size:22px;line-height:1.35}.training-context-title .el-tag{flex-shrink:0}.training-context-copy p{margin:0;color:#687563;font-size:12px;line-height:1.7}.training-context-select{display:grid;align-content:center;gap:7px;padding-left:20px;border-left:1px solid #dce1d4}.training-context-select label{color:#56634d;font-size:12px;font-weight:600}.training-context-select :deep(.el-select){width:100%}.training-layout{display:grid;grid-template-columns:minmax(320px,.86fr) minmax(0,1.14fr);gap:16px;align-items:start}.training-panel{min-width:0;border:1px solid #dce1d4;border-radius:14px;background:#fcfcf8;box-shadow:0 10px 24px rgba(41,75,60,.045);overflow:hidden}.training-panel-heading{display:grid;grid-template-columns:40px minmax(0,1fr) auto;align-items:start;gap:12px;padding:22px 24px 18px;border-bottom:1px solid #e3e8dd}.training-step-mark{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:#e4e9da;color:#294b3c;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}.training-panel-heading>div{display:grid;gap:3px;min-width:0}.training-panel-kicker{color:#81907d;font-size:9px;letter-spacing:.14em}.training-panel-heading h2{margin:0;color:#294b3c;font-size:18px;line-height:1.4}.training-panel-heading p{margin:1px 0 0;color:#687563;font-size:12px;line-height:1.65}.training-live-dot{align-self:center;white-space:nowrap;padding:5px 9px;border-radius:999px;background:#edf0e7;color:#74806e;font-size:11px}.training-live-dot.active{background:#e4e9da;color:#294b3c}.training-card-context{display:grid;gap:7px;margin:20px 24px 0;padding:15px 16px;border:1px solid #dce1d4;border-radius:10px;background:#f4f5ed}.training-card-context>span{color:#7b8776;font-size:11px}.training-card-context>strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#294b3c;font-size:15px}.training-keywords{display:flex;align-items:center;flex-wrap:wrap;gap:6px;min-height:22px}.training-keywords small{color:#7b8776;font-size:11px}.training-form-block{display:grid;gap:9px;margin:18px 24px 0}.training-label-row{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#56634d;font-size:12px;font-weight:600}.training-label-row small{color:#8b9586;font-size:11px;font-weight:400}.training-primary-action{width:max-content;min-width:104px;margin:0}.training-empty-state{display:grid;justify-items:center;gap:7px;min-height:190px;margin:20px 24px 24px;padding:28px 20px;border:1px dashed #ccd7c6;border-radius:12px;background:#f7f8f2;text-align:center}.training-empty-mark{display:grid;place-items:center;width:46px;height:46px;border-radius:50%;background:#e4e9da;color:#5d7350;font-size:12px}.training-empty-state strong{color:#294b3c;font-size:14px}.training-empty-state p{margin:0;color:#7b8776;font-size:12px}.training-result-panel{margin:20px 24px 0;padding:16px;border:1px solid #dce1d4;border-radius:10px;background:#f7f8f2}.training-result-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;color:#294b3c;font-size:13px;font-weight:600}.training-result-heading small{color:#7b8776;font-size:11px;font-weight:400}.training-result-panel h3{margin:0 0 12px;color:#294b3c;font-size:16px;line-height:1.5}.training-result-panel .cloze-text{margin:14px 0;line-height:2}.training-result-panel .cloze-inputs{display:grid;gap:8px;margin:14px 0}.recall-panel>.el-result{padding:18px 24px 24px}.training-stage-list{padding:0 24px 8px}.training-stage-item{display:grid;grid-template-columns:30px minmax(0,1fr);gap:14px;padding:22px 0;border-top:1px solid #e3e8dd}.training-stage-item:first-child{border-top:0}.training-stage-index{display:grid;place-items:center;width:28px;height:28px;border:1px solid #cbd7c9;border-radius:9px;color:#5d7350;font-size:11px;font-weight:700}.training-stage-content{display:grid;gap:11px;min-width:0}.training-stage-title{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.training-stage-title strong{color:#294b3c;font-size:14px}.training-stage-title small{color:#7b8776;font-size:11px}.training-stage-content :deep(.el-slider){margin:2px 5px 0}.training-stage-content :deep(.el-textarea__inner){min-height:112px}.training-secondary-action{width:max-content;margin:0}.training-stage-content :deep(.el-result){padding:10px 0 0;text-align:left}.training-stage-content :deep(.el-result__icon){display:none}.training-stage-content :deep(.el-result__title),.training-stage-content :deep(.el-result__subtitle){text-align:left}.audio-player{width:100%;max-width:420px}.speech-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.speech-controls .el-button + .el-button { margin-left: 0; }
.knowledge-card-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}.knowledge-card-title{display:flex;align-items:center;gap:8px;min-width:0;flex:1 1 160px;overflow-wrap:anywhere}.knowledge-card-tools{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-left:auto}.knowledge-card-tools :deep(.el-button+.el-button){margin-left:0}
.split-visual{display:grid;gap:10px;margin-top:14px;padding:14px;border:1px solid #dce8e5;border-radius:12px;background:#f7fbfa}.split-heading{display:flex;justify-content:space-between;gap:16px;color:#315b55}.split-heading span{font-size:12px;color:#718580}.split-preview{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:12px;align-items:stretch}.split-preview article{min-width:0;padding:12px;border:1px solid #dfe9e6;border-radius:9px;background:#fff}.split-preview pre{max-height:180px;margin:9px 0 0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px;line-height:1.6;color:#45645f}.split-preview>i{display:grid;place-items:center;padding:0 4px;border-left:2px dashed #e69b48;color:#a75d16;font-size:12px;font-style:normal;writing-mode:vertical-rl}@media(max-width:700px){.split-preview{grid-template-columns:1fr}.split-preview>i{border-left:0;border-top:2px dashed #e69b48;writing-mode:horizontal-tb;padding:7px}}
.student-two-column{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.learning-history{min-width:0}.history-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}.history-heading h3{margin:0}.history-heading p{margin:5px 0 0;color:#81918b;font-size:12px}.history-count{flex:0 0 auto;padding:4px 9px;border-radius:999px;background:#edf0e7;color:#294b3c;font-size:12px}.history-list{display:grid;gap:8px}.history-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:12px;padding:11px 12px;border:1px solid #e3ece8;border-radius:10px;background:linear-gradient(135deg,#fff,#f8fcfa);transition:border-color .15s,box-shadow .15s,transform .15s}.history-row:hover{border-color:#9bc5bb;box-shadow:0 5px 14px #31544812;transform:translateY(-1px)}.history-row-info{display:grid;min-width:0;gap:5px}.history-row-info>strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#294a42;font-size:13px}.history-kind{width:max-content;padding:3px 7px;border-radius:5px;font-size:11px;font-weight:700}.history-kind.memory{background:#edf0e7;color:#294b3c}.history-kind.practice{background:#edf0e7;color:#5d7350}.history-kind.published{background:#fff4e8;color:#a86b22}.history-score{display:grid;justify-items:end;min-width:60px}.history-score>strong{font-size:18px;line-height:1}.history-score>small{margin-top:3px;color:#8a9994;font-size:11px}.history-score.good>strong{color:#526747}.history-score.normal>strong{color:#8b5b28}.history-score.low>strong{color:#a34f28}.history-row>time{color:#899793;font-size:11px;white-space:nowrap}@media(max-width:700px){.student-two-column{grid-template-columns:1fr}}@media(max-width:600px){.history-row{grid-template-columns:minmax(0,1fr) auto;gap:8px}.history-row>time{grid-column:1/-1}.history-score{min-width:54px}}
.block-header-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.published-knowledge-list{display:grid;gap:10px;margin-top:14px;max-height:min(58vh,620px);overflow:auto;padding-right:4px}.published-knowledge-item{padding:13px 14px;border:1px solid #dfe9e6;border-radius:10px;background:#fcfcf8}.published-knowledge-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.published-knowledge-item p{margin:8px 0 6px;color:#526b64;line-height:1.65;white-space:pre-wrap}.published-knowledge-item small{display:block}

/* A3: course navigation / learning canvas / evidence, with artwork outside reading areas. */
.student-workspace{--study-ease:cubic-bezier(.16,1,.3,1);max-width:1920px!important;padding:0 22px 28px!important;margin:0 auto;background:#dfe3d5;min-height:100dvh;color:#293c30}
.personal-course-card{align-self:start}
.personal-course-form{display:grid;gap:18px;min-width:0;align-content:start}
.personal-course-form>.el-button{justify-self:start;margin:2px 0 0}
.personal-course-card :deep(.el-input__inner:focus-visible),.personal-course-card :deep(.el-textarea__inner:focus-visible){outline:none}
.personal-course-card :deep(.el-input__wrapper.is-focus),.personal-course-card :deep(.el-textarea__inner:focus){box-shadow:0 0 0 2px #355d4b inset}
.student-topbar{padding-inline:clamp(12px,1.5vw,26px);background:#fcfcf8}
.student-topbar{height:78px;display:flex;align-items:center;gap:36px;border-bottom:1px solid #e0e8e3;margin-bottom:18px}.student-brand{display:flex;align-items:center;gap:12px;min-width:208px;text-decoration:none;color:#193e38;font-size:22px;font-weight:650}.student-brand>.el-icon{font-size:32px;color:#294b3c}.student-brand small{display:block;font-size:10px;font-weight:400;letter-spacing:.15em;color:#56634d;margin-top:3px}.student-topbar nav{display:flex;gap:32px;align-self:stretch;align-items:center;flex:1}.student-topbar nav a{font-size:14px;text-decoration:none;color:#56634d;height:100%;display:flex;align-items:center;position:relative;white-space:nowrap}.student-topbar nav a.router-link-active{color:#294b3c;font-weight:600}.student-topbar nav a.router-link-active::after{content:'';position:absolute;bottom:12px;left:0;right:0;height:2px;background:#294b3c;border-radius:2px}.account-menu{gap:12px;max-width:230px}.account-menu :deep(span){overflow:hidden;text-overflow:ellipsis}
.student-app-grid{display:grid;grid-template-columns:210px minmax(0,1fr);gap:18px;align-items:start}.student-main{min-width:0}.student-course-nav{position:sticky;top:18px;min-height:calc(100dvh - 120px);max-height:calc(100dvh - 36px);display:flex;flex-direction:column;background:#fcfcf8;border:1px solid #dce1d4;border-radius:12px;padding:18px 10px 0;overflow:auto}.course-nav-heading{display:flex;align-items:center;justify-content:space-between;padding:0 6px 8px;gap:10px}.course-nav-toggle{border:0;background:none;font:inherit;font-size:15px;font-weight:600;color:#293c30;padding:4px;cursor:pointer}.course-nav-toggle .el-icon{display:none}.course-nav-group{padding:16px 0}.course-nav-group+.course-nav-group{border-top:1px solid #dce1d4}.course-nav-group h2{font-size:13px;margin:0 10px 7px}.course-nav-group p{font-size:11px;line-height:1.7;color:#56634d;margin:0 10px 12px}.course-nav-item{display:flex;align-items:center;text-align:left;gap:10px;width:100%;padding:12px 11px;border:0;border-radius:7px;margin:3px 0;background:transparent;color:#495741;font:inherit;font-size:13px;cursor:pointer;min-width:0}.course-nav-item span{overflow-wrap:anywhere;line-height:1.5}.course-nav-item .el-icon{font-size:17px;flex-shrink:0}.course-nav-item.selected{background:#e4e9da;color:#294b3c;font-weight:600}.course-nav-item:hover{background:#edf0e7}.course-nav-art{margin-top:auto;padding-top:26px;overflow:hidden}.course-nav-art>span{display:block;font-size:11px;color:#56634d;margin:0 10px 22px}.student-header{position:relative;display:flex;align-items:center;min-height:76px;margin:0 0 14px;padding:6px 16px;overflow:hidden}.student-header .page-title{display:flex;gap:20px;align-items:baseline;position:relative;z-index:1}.student-header h1{font-size:28px;letter-spacing:-.025em;margin:0}.student-header .study-artwork{position:absolute;right:0;top:-8px;width:138px;height:96px;opacity:.5}.student-header p{font-size:13px;max-width:40ch}.course-strip{margin-bottom:14px;border-color:#dce1d4;border-radius:10px}.course-strip :deep(.el-card__body){padding:12px 16px}.course-strip-main{display:flex;align-items:center;gap:12px}.course-selector{display:grid;grid-template-columns:auto minmax(150px,240px);gap:10px;align-items:center;flex:1}.course-selector>.muted{grid-column:1/-1;font-size:12px}.course-selector label{font-size:12px;font-weight:600}.learning-workspace{margin-top:0}.student-workspace-tabs :deep(.el-tabs__header){margin:0 0 14px;background:#fcfcf8;border:1px solid #dce1d4;border-radius:9px;padding:0 12px}.student-workspace-tabs :deep(.el-tabs__item){height:49px;font-size:13px;padding:0 16px}.student-workspace-tabs :deep(.el-tabs__nav-wrap::after){display:none}.student-workspace-tabs :deep(.el-tabs__active-bar){height:3px;background:#294b3c}.student-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,32%);gap:16px}.student-grid>*{min-width:0}.student-workspace-tabs :deep(.el-card){border-color:#dce1d4;border-radius:10px}.student-workspace-tabs :deep(.el-card__header){padding:16px 18px}.student-workspace-tabs :deep(.el-card__body){padding:18px}.qa-panel :deep(>.el-card__body){display:flex;flex-direction:column;min-height:480px}.qa-composer{order:5;margin-top:20px;padding-top:18px;border-top:1px solid #e4ece7}.qa-composer>label{display:block;font-size:12px;color:#56634d;margin-bottom:8px}.qa-composer>.el-select{margin-bottom:10px}.qa-composer>.form-button{margin-top:10px}.qa-welcome{margin:auto 0;padding:40px 22px;max-width:55ch}.qa-welcome>.el-icon{font-size:30px;color:#294b3c;margin-bottom:14px}.qa-welcome h2{font-size:24px;font-weight:600;margin:0 0 12px}.qa-welcome p{font-size:14px;line-height:1.8;margin:0 0 8px;color:#56634d}.qa-welcome>span{font-size:12px;color:#56634d}.dialogue{margin:0}.dialogue-row{padding:18px 16px;border-radius:8px}.dialogue-row p{font-size:14px;line-height:1.9}.dialogue-row.student{background:#eef5f2}.dialogue-row.assistant{background:transparent}.source-jump{border:1px solid #dce1d4;border-radius:8px;padding:12px;gap:12px;background:#fcfcf8}.source-jump>span{overflow-wrap:anywhere}.profile-column{display:flex;flex-direction:column;gap:16px}.profile-column :deep(.el-card){background:#fcfcf8}.source-inspector{border:1px solid #dce1d4;border-radius:10px;background:#fcfcf8;overflow:hidden}.source-inspector-heading{display:flex;justify-content:space-between;align-items:center;gap:8px;min-height:53px;padding:12px 16px;border-bottom:1px solid #e4ece7}.source-inspector-heading h2{font-size:14px;margin:0;font-weight:600}.source-inspector-heading .el-button{margin:0;padding:4px}.source-placeholder{padding:34px 22px;min-height:245px}.source-placeholder>.el-icon{font-size:32px;color:#729889}.source-placeholder h3{font-size:15px;margin:20px 0 10px}.source-placeholder p{font-size:13px;line-height:1.8;color:#56634d}.source-placeholder>span{font-size:11px;color:#56634d}.source-reference-list{padding:12px}.source-reference{width:100%;display:flex;align-items:center;gap:10px;text-align:left;font:inherit;font-size:12px;padding:14px 8px;background:transparent;border:0;border-bottom:1px solid #e4ece7;color:#294b3c;cursor:pointer}.source-reference>span:nth-child(2){flex:1;min-width:0}.source-reference strong,.source-reference small{display:block;overflow-wrap:anywhere;line-height:1.7}.source-reference small{color:#56634d}.source-inspector :deep(.student-material-preview){border:0;margin:0}.source-inspector :deep(.preview-body iframe){height:410px;min-height:260px}.source-inspector :deep(.preview-header){flex-wrap:wrap}.source-inspector :deep(.preview-header b){font-size:12px}.source-inspector :deep(.preview-pagination){flex-wrap:wrap;font-size:12px}.source-inspector :deep(.preview-toolbar){flex-wrap:wrap}.source-inspector :deep(.preview-toolbar .el-select){flex-basis:100%}.source-inspector :deep(.el-card__body){padding:14px}.card-view-switch{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:24px}.card-view-switch>span{font-size:13px;color:#56634d}.student-workspace :deep(.learning-focus){margin-top:18px}.student-workspace :deep(.el-input__inner),.student-workspace :deep(.el-textarea__inner){caret-color:#294b3c}.student-workspace :deep(.el-textarea__inner::placeholder){color:#68705e}.student-workspace :deep(.el-input__inner::placeholder){color:#68705e}.student-workspace :deep(:focus-visible){outline:2px solid #294b3c;outline-offset:3px}.student-workspace ::selection{background:#dce3d3;color:#294b3c}
@media(prefers-reduced-motion:no-preference){.course-nav-item{transition:background .18s,transform .22s var(--study-ease)}.course-nav-item:hover{transform:translateX(3px)}.student-workspace :deep(.el-button){transition:transform .18s var(--study-ease),background-color .18s}.student-workspace :deep(.el-button:active:not(:disabled)){transform:scale(.97)}.source-slide-enter-active,.source-slide-leave-active{transition:transform .24s var(--study-ease),opacity .18s}.source-slide-enter-from{transform:translateX(20px);opacity:0}.source-slide-leave-to{transform:translateX(8px);opacity:0}.knowledge-card{transition:transform .22s var(--study-ease)}.knowledge-card:hover{transform:translateY(-3px)}}
@media(prefers-reduced-motion:no-preference){.upload-status.is-processing{animation:upload-status-pulse 1.8s ease-in-out infinite}.upload-status.is-processing :deep(.el-alert__title)::after{content:'';display:inline-block;width:5px;height:5px;margin-left:8px;vertical-align:middle;border-radius:50%;background:#5d7350;box-shadow:0 0 0 0 #5d735066;animation:upload-status-dot 1.4s ease-out infinite}.source-jump{transition:transform .2s var(--study-ease),border-color .18s,background-color .18s}.source-jump:hover{transform:translateY(-2px);border-color:#9dbba8;background:#f2faf5}.knowledge-card{animation:knowledge-card-enter .38s var(--study-ease) both;animation-delay:calc(var(--motion-index, 0) * 35ms)}}
@keyframes upload-status-pulse{0%,100%{box-shadow:0 0 0 0 rgba(93,115,80,0)}50%{box-shadow:0 6px 18px -14px rgba(41,75,60,.58)}}
@keyframes upload-status-dot{70%,100%{box-shadow:0 0 0 7px transparent}}
@keyframes knowledge-card-enter{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media(min-width:1500px){.course-selector{grid-template-columns:auto minmax(180px,260px) minmax(0,1fr)}.course-selector>.muted{grid-column:auto}}
@media(max-width:1200px){.student-app-grid{grid-template-columns:180px minmax(0,1fr);gap:14px}.student-grid{grid-template-columns:minmax(0,1fr) 270px}.student-workspace{padding:0 16px 24px!important}.student-topbar{gap:20px}.student-brand{min-width:176px}.student-topbar nav{gap:20px}.student-header .page-title{display:block}.student-header p{margin-top:8px}.course-strip-main{flex-wrap:wrap}.course-selector{flex-basis:100%}.student-workspace-tabs :deep(.el-tabs__item){padding:0 12px}}
@media(max-width:1000px){.student-grid{grid-template-columns:minmax(0,1fr)}.profile-column{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.student-topbar{flex-wrap:wrap;height:auto;min-height:76px;padding:12px 0;gap:14px}.student-topbar nav{order:3;flex-basis:100%;height:38px}.student-topbar nav a.router-link-active::after{bottom:0}.student-topbar .el-dropdown{margin-left:auto}.source-inspector :deep(.preview-body iframe){height:360px}}
@media(max-width:760px){.student-workspace{padding:0 12px 20px!important}.student-app-grid{grid-template-columns:minmax(0,1fr);gap:12px}.student-course-nav{position:static;min-height:0;max-height:none;padding:10px 12px}.course-nav-heading{padding:0}.course-nav-toggle{display:flex;align-items:center;gap:10px}.course-nav-toggle .el-icon{display:inline-flex}.course-nav-groups{display:none}.course-nav-groups.is-open{display:block}.course-nav-art{display:none}.student-brand{font-size:19px;min-width:0}.student-brand>.el-icon{font-size:26px}.account-menu{max-width:160px}.student-topbar nav{gap:22px}.student-topbar nav a{font-size:13px}.student-header{padding:8px 2px;margin-bottom:10px;min-height:70px}.student-header h1{font-size:25px}.student-header p{font-size:12px}.student-header>.study-artwork{opacity:.3;right:-28px;width:112px;height:84px}.course-selector{grid-template-columns:auto minmax(0,1fr)}.course-strip-main>.el-tag{max-width:100%;white-space:normal;height:auto;min-height:24px}.profile-column{display:flex}.student-workspace-tabs :deep(.el-card__header),.student-workspace-tabs :deep(.el-card__body){padding:16px}.qa-panel :deep(>.el-card__body){min-height:420px}.qa-welcome{padding:26px 4px}.qa-welcome h2{font-size:22px}.source-jump{flex-wrap:wrap}.student-workspace-tabs :deep(.el-tabs__header){padding:0 8px}.card-view-switch{flex-wrap:wrap}.source-placeholder{min-height:0;padding:24px}.card-header{flex-wrap:wrap}}
@media(max-width:980px){.training-context-card{grid-template-columns:1fr}.training-context-select{padding:16px 0 0;border-top:1px solid #dce1d4;border-left:0}.training-layout{grid-template-columns:minmax(0,1fr)}}
@media(max-width:760px){.training-context-card{padding:18px}.training-context-title{align-items:flex-start;flex-direction:column;gap:6px}.training-context-title b{white-space:normal;font-size:19px}.training-panel-heading{padding:18px 16px 16px;grid-template-columns:34px minmax(0,1fr)}.training-panel-heading>.el-tag,.training-live-dot{grid-column:2;justify-self:start}.training-card-context,.training-form-block,.training-empty-state,.training-result-panel{margin-left:16px;margin-right:16px}.training-stage-list{padding-inline:16px}.training-stage-title{align-items:flex-start;flex-direction:column;gap:4px}.training-panel-heading h2{font-size:17px}}

.training-form-block > .training-primary-action{margin-top:8px}
</style>
