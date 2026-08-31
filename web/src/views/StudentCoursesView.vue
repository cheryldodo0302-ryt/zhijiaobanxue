<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { useAuthStore } from "../stores/auth";
import KnowledgeGraphCanvas from "../components/KnowledgeGraphCanvas.vue";
import AiSettingsDialog from "../components/AiSettingsDialog.vue";
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
const loading = ref(false);
const aiSettingsOpen = ref(false);
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
const messages = ref<any[]>([]);
const quiz = ref<any>(null);
const responses = ref<any[]>([]);
const grade = ref<any>(null);
const profile = ref<any>(null);
const retrievalMaterial = ref("all");
const documents = ref<any[]>([]);
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
const trainingBlockId = ref<number | undefined>(undefined);
const extraKeywords = ref("");
const cloze = ref<any>(null);
const clozeResponses = ref<string[]>([]);
const clozeResult = ref<any>(null);
const recitedText = ref("");
const recitationResult = ref<any>(null);
const speechRate = ref(1);
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
const publishedResponses = ref<any[]>([]);
const publishedGrade = ref<any>(null);
const publishedGraph = ref<any>(null);
const graphSelectedNode = ref<any>(null);
const graphSearch = ref("");
const graphLayout = ref<"force" | "circular">("force");

const selectedCourse = computed(() =>
  courses.value.find((item) => item.course_id === courseId.value),
);
const materialPartitions = computed(
  () => selectedCourse.value?.material_partitions || [],
);
const trainingBlock = computed(() =>
  blocks.value.find((item) => item.block_id === trainingBlockId.value),
);

function actionScope(id = courseId.value) {
  return id ? { course_id: id } : {};
}

async function invoke(
  action: string,
  input: Record<string, any> = {},
  id = courseId.value,
) {
  if (!auth.user) throw new Error("登录状态已失效，请重新登录");
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
  });
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
    if (!courseId.value && courses.value.length) {
      courseId.value = courses.value.some(
        (item) => item.course_id === requestedCourse,
      )
        ? requestedCourse
        : courses.value[0].course_id;
    }
    await courseChanged();
  } catch (error) {
    showError(error, "课程加载失败");
  } finally {
    loading.value = false;
  }
}

async function courseChanged() {
  retrievalMaterial.value = "all";
  session.value = null;
  messages.value = [];
  quiz.value = null;
  grade.value = null;
  publishedBank.value = null;
  publishedGrade.value = null;
  trainingBlockId.value = undefined;
  activeTab.value = normalizeStudentView(
    activeTab.value,
    selectedCourse.value?.course_type === "shared_course",
  );
  await loadCourseData();
  await loadPublishedGraph();
}

async function loadPublishedGraph() {
  publishedGraph.value = null;
  graphSelectedNode.value = null;
  if (!courseId.value || selectedCourse.value?.course_type !== "shared_course")
    return;
  try {
    publishedGraph.value = (
      await api.get(`/student/courses/${courseId.value}/knowledge-graph`)
    ).data;
  } catch (error: any) {
    if (error?.response?.status !== 404)
      showError(error, "课程知识图谱加载失败");
  }
}

async function loadCourseData() {
  if (!courseId.value) return;
  try {
    const [docs, nextBlocks, nextProfile, nextDashboard] = await Promise.all([
      invoke("document_status"),
      invoke("knowledge_blocks_list"),
      invoke("learning_profile"),
      invoke("student_dashboard"),
    ]);
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
      { role: "assistant", content: result.reply },
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
      { role: "assistant", content: result.reply },
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
      courseId.value = created.course_id;
      await courseChanged();
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
    blocks.value = [...blocks.value, ...(result || [])];
    ElMessage.success(`已生成 ${(result || []).length} 个知识卡片`);
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
      block_id: trainingBlockId.value,
      extra_keywords: cloze.value.extra_keywords || [],
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
  if (!trainingBlock.value || !("speechSynthesis" in window))
    return ElMessage.warning("当前浏览器不支持朗读");
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(trainingBlock.value.content);
  utterance.lang = "zh-CN";
  utterance.rate = speechRate.value;
  window.speechSynthesis.speak(utterance);
}

async function evaluateRecitation() {
  if (!trainingBlockId.value || !recitedText.value.trim())
    return ElMessage.warning("请先输入或粘贴你的背诵内容");
  loading.value = true;
  try {
    recitationResult.value = await invoke("recitation_evaluate", {
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
    showError(error, "AI 练习生成失败");
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
  loading.value = true;
  try {
    publishedGrade.value = await invoke("quiz_submit", {
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

async function logout() {
  await auth.logout();
  location.href = "/login";
}
function updateAiStatus(settings: any) {
  aiStatus.value = settings;
}
watch([courseId, activeTab], ([course, tab]) => {
  const query = {
    ...route.query,
    course: course || undefined,
    view: tab || undefined,
  };
  if (
    String(route.query.course || "") !== course ||
    String(route.query.view || "") !== tab
  )
    router.replace({ query });
});
onMounted(async () => {
  activeTab.value = normalizeStudentView(route.query.view, true);
  await loadCourses();
  try {
    aiStatus.value = (await api.get("/runtime/ai-settings")).data;
  } catch {
    aiStatus.value = null;
  }
});
onUnmounted(async () => {
  if (recorder?.state === "recording") recorder.stop();
  recorderStream?.getTracks().forEach((track) => track.stop());
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  await stopAudioMonitor(monitorHandle);
  monitorHandle = null;
});
</script>

<template>
  <main class="content student-workspace" :aria-busy="loading">
    <header class="student-header">
      <div class="page-title">
        <span class="eyebrow">课程学习</span>
        <h1>今天从哪一个问题开始？</h1>
        <p class="muted">
          课程资料、答疑、知识卡片和练习都在同一个学习空间里，按自己的节奏继续。
        </p>
      </div>
      <div class="student-account">
        <el-tag
          v-if="aiStatus"
          :type="aiStatus.configured ? 'success' : 'warning'"
          >{{ aiStatus.provider }} · {{ aiStatus.model }}</el-tag
        ><el-button plain @click="aiSettingsOpen = true">AI 服务设置</el-button
        ><el-button plain @click="$router.push('/student/study-room')"
          >AI 自习室</el-button
        ><span>{{ auth.user?.display_name || auth.user?.username }}</span
        ><el-button @click="logout">退出</el-button>
      </div>
    </header>
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
      class="upload-status"
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
          <label>当前课程</label
          ><el-select
            v-if="courses.length"
            v-model="courseId"
            @change="courseChanged"
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
        <el-button text @click="loadCourses">刷新</el-button>
      </div>
      <div v-if="selectedCourse" class="course-summary-cards">
        <div class="course-summary-card">
          <div class="course-summary-title">
            <span>课程资料</span><em>学习空间</em>
          </div>
          <strong>{{ documents.length }}</strong
          ><small>已解析材料</small>
        </div>
        <div class="course-summary-card">
          <div class="course-summary-title">
            <span>知识卡片</span><em>复习内容</em>
          </div>
          <strong>{{ blocks.length }}</strong
          ><small>可复习内容</small>
        </div>
        <div class="course-summary-card">
          <div class="course-summary-title">
            <span>背诵平均</span><em>记忆训练</em>
          </div>
          <strong>{{ dashboard?.memory_average || 0 }}%</strong
          ><small>挖空与背诵</small>
        </div>
        <div class="course-summary-card">
          <div class="course-summary-title">
            <span>练习平均</span><em>作答表现</em>
          </div>
          <strong>{{ dashboard?.practice_average || 0 }}%</strong
          ><small>AI 练习成绩</small>
        </div>
      </div>
    </el-card>
    <el-empty
      v-if="!courses.length"
      description="暂无已授权课程，请联系任课教师或创建个人课程"
    />
    <el-card v-if="!courseId" shadow="never" class="empty-course-card"
      ><template #header><b>先创建一个属于自己的学习空间</b></template>
      <p class="muted">
        个人课程适合整理教材、讲义或自己的复习材料；内容只对你可见。
      </p>
      <div class="student-two-column">
        <el-input
          v-model="newCourseName"
          placeholder="课程名称，例如：细胞生物学背诵"
        /><el-input
          v-model="newCourseDescription"
          placeholder="课程说明（可选）"
        />
      </div>
      <el-button
        type="primary"
        class="form-button"
        @click="createPersonalCourse"
        >创建个人课程</el-button
      ></el-card
    >
    <el-tabs
      v-if="courseId"
      v-model="activeTab"
      class="student-workspace-tabs"
      stretch
    >
      <el-tab-pane name="qa" label="学习问答">
        <div class="student-grid">
          <div class="learning-column">
            <el-card shadow="never"
              ><template #header
                ><div class="card-header">
                  <b>引导式答疑</b><span class="muted">只引用当前课程资料</span>
                </div></template
              ><el-select
                v-if="materialPartitions.length"
                v-model="retrievalMaterial"
                :disabled="!!session && !session.completed"
                placeholder="选择答疑资料范围"
                ><el-option label="全部已发布资料" value="all" /><el-option
                  v-for="item in materialPartitions"
                  :key="item.material_type"
                  :label="item.label || item.material_type"
                  :value="item.material_type" /></el-select
              ><el-input
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
            <el-card shadow="never"
              ><template #header
                ><div class="card-header">
                  <b>我的学习情况</b
                  ><el-button text @click="activeTab = 'profile'"
                    >查看全部</el-button
                  >
                </div></template
              >
              <p class="muted">数据仅来自你在当前课程的问答和练习。</p>
              <div class="profile-metrics">
                <span
                  >问答 <b>{{ profile?.questions?.length || 0 }}</b></span
                ><span
                  >练习 <b>{{ profile?.attempts?.length || 0 }}</b></span
                >
              </div>
              <h3>需要复习</h3>
              <el-empty
                v-if="!profile?.weak_points?.length"
                description="完成练习后生成"
                :image-size="72"
              />
              <div
                v-for="point in profile?.weak_points || []"
                :key="point.knowledge_point"
                class="weak-point"
              >
                <span>{{ point.knowledge_point }}</span
                ><el-tag type="warning">{{ point.level }}</el-tag>
              </div></el-card
            >
          </aside>
        </div>
      </el-tab-pane>
      <el-tab-pane name="materials" label="课程与材料"
        ><div class="student-two-column">
          <el-card shadow="never"
            ><template #header><b>创建个人课程</b></template
            ><el-input
              v-model="newCourseName"
              placeholder="例如：细胞生物学背诵"
            /><el-input
              v-model="newCourseDescription"
              type="textarea"
              :rows="3"
              class="stack-input"
              placeholder="课程说明（可选）"
            /><el-button type="primary" @click="createPersonalCourse"
              >创建并开始整理</el-button
            ><el-divider
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
                >调用视觉模型并保存文字</el-button
              ></template
            ></el-card
          >
        </div>
        <el-card v-if="selectedCourse" shadow="never" class="nested-card"
          ><template #header
            ><div class="card-header">
              <b>已解析材料</b
              ><el-button text @click="loadCourseData">刷新</el-button>
            </div></template
          ><el-empty
            v-if="!documents.length"
            description="还没有课程材料"
            :image-size="72"
          />
          <div
            v-for="document in documents"
            :key="document.document_id"
            class="document-row"
          >
            <div>
              <strong>{{ document.original_name }}</strong>
              <p class="muted">
                {{ document.chunk_count }} 个文字片段 · {{ document.status }}
              </p>
              <p class="document-preview">
                {{ document.text_preview || "暂无文字预览" }}
              </p>
            </div>
            <el-button
              v-if="selectedCourse.course_type === 'personal_course'"
              type="danger"
              text
              @click="deleteDocument(document.document_id)"
              >删除</el-button
            >
          </div></el-card
        ></el-tab-pane
      >
      <el-tab-pane name="blocks" label="知识卡片"
        ><el-card shadow="never"
          ><template #header
            ><div class="card-header">
              <div>
                <b>把材料整理成可复习的卡片</b>
                <p class="muted small">
                  AI 只根据当前课程文字分块，你可以继续手动调整。
                </p>
              </div>
              <el-button
                type="primary"
                :disabled="!documents.length"
                @click="buildBlocks"
                >AI 语义分块</el-button
              >
            </div></template
          ><el-empty
            v-if="!blocks.length"
            description="请先导入材料并生成知识卡片"
          />
          <div v-else class="knowledge-card-grid">
            <el-card
              v-for="block in blocks"
              :key="block.block_id"
              shadow="never"
              class="knowledge-card"
              ><div class="card-header">
                <b>{{ block.title }}</b
                ><el-tag v-if="block.is_favorite" type="warning">重点</el-tag>
              </div>
              <p class="card-content">{{ block.content }}</p>
              <div class="keyword-list">
                <el-tag
                  v-for="keyword in block.keywords || []"
                  :key="keyword"
                  effect="plain"
                  >{{ keyword }}</el-tag
                >
              </div>
              <div class="block-actions">
                <el-button size="small" @click="openBlock(block)"
                  >编辑</el-button
                ><el-button size="small" @click="mergeBlock(block)"
                  >合并下一张</el-button
                >
              </div></el-card
            >
          </div></el-card
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
          <div class="split-row">
            <el-input-number
              v-model="splitPosition"
              :min="20"
              :max="Math.max(20, editContent.length - 20)"
              placeholder="拆分位置"
            /><el-button @click="splitBlock">从此处拆分</el-button>
          </div>
          <template #footer
            ><el-button @click="closeBlock">取消</el-button
            ><el-button type="primary" @click="saveBlock"
              >保存卡片</el-button
            ></template
          ></el-dialog
        ></el-tab-pane
      >
      <el-tab-pane name="training" label="训练巩固"
        ><el-card shadow="never"
          ><template #header><b>选择一张知识卡片</b></template
          ><el-select v-model="trainingBlockId" placeholder="选择训练卡片"
            ><el-option
              v-for="block in blocks"
              :key="block.block_id"
              :label="block.title"
              :value="block.block_id" /></el-select
        ></el-card>
        <div v-if="trainingBlock" class="student-two-column training-columns">
          <el-card shadow="never"
            ><template #header><b>关键词挖空</b></template
            ><el-input
              v-model="extraKeywords"
              placeholder="手动追加重点词，逗号分隔" /><el-button
              type="primary"
              class="form-button"
              @click="generateCloze"
              >生成挖空</el-button
            >
            <div v-if="cloze" class="cloze-panel">
              <h3>{{ cloze.title }}</h3>
              <p class="cloze-text">
                <template
                  v-for="(segment, index) in cloze.segments"
                  :key="index"
                  ><span v-if="segment.type === 'text'">{{
                    segment.value
                  }}</span
                  ><el-tag v-else type="warning"
                    >第 {{ segment.index }} 空</el-tag
                  ></template
                >
              </p>
              <div class="cloze-inputs">
                <el-input
                  v-for="(_, index) in clozeResponses"
                  :key="index"
                  v-model="clozeResponses[Number(index)]"
                  :placeholder="`第 ${Number(index) + 1} 空`"
                />
              </div>
              <el-button type="primary" @click="submitCloze"
                >提交并检测</el-button
              >
            </div>
            <el-result
              v-if="clozeResult"
              :icon="clozeResult.score >= 60 ? 'success' : 'warning'"
              :title="`正确率 ${clozeResult.score}%`"
              :sub-title="`答对 ${clozeResult.correct_count} / ${clozeResult.total} 空`" /></el-card
          ><el-card shadow="never"
            ><template #header><b>听觉强化与跟读</b></template>
            <p class="muted">
              先听一遍，再用自己的话复述；浏览器会在本地朗读，不上传录音。
            </p>
            <el-slider
              v-model="speechRate"
              :min="0.75"
              :max="2"
              :step="0.25"
              show-stops /><el-button type="primary" @click="speakBlock"
              >朗读当前卡片</el-button
            ><el-divider /><el-input
              v-model="recitedText"
              type="textarea"
              :rows="6"
              placeholder="粘贴或输入你的复述内容，交给当前模型检测" /><el-button
              class="form-button"
              @click="evaluateRecitation"
              >检测复述</el-button
            ><el-result
              v-if="recitationResult"
              :title="`背诵评分 ${recitationResult.score}`"
              :sub-title="recitationResult.feedback" /><el-divider /><el-button
              plain
              @click="toggleRecording"
              >{{ recording ? "停止录音" : "录一段跟读" }}</el-button
            ><audio
              v-if="audioUrl"
              :src="audioUrl"
              controls
              class="audio-player"
          /></el-card>
        </div>
        <el-empty v-else description="请先在知识卡片中生成至少一张卡片"
      /></el-tab-pane>
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
          <div v-if="publishedBank?.items?.length" class="question-list">
            <p class="muted">
              题库版本 v{{ publishedBank.version_number }} · 共
              {{ publishedBank.total }} 题
            </p>
            <article
              v-for="(item, index) in publishedBank.items"
              :key="item.item_id"
              class="question-item"
            >
              <b>{{ Number(index) + 1 }}. {{ item.question }}</b
              ><el-checkbox-group
                v-if="isMultiple(item)"
                v-model="publishedResponses[Number(index)]"
                ><el-checkbox
                  v-for="option in item.options || []"
                  :key="option.key || option"
                  :label="option.key || option"
                  >{{ option.text || option }}</el-checkbox
                ></el-checkbox-group
              ><el-radio-group
                v-else-if="isChoice(item)"
                v-model="publishedResponses[Number(index)]"
                ><el-radio
                  v-for="option in item.options || ['正确', '错误']"
                  :key="option.key || option"
                  :value="option.key || option"
                  >{{ option.text || option }}</el-radio
                ></el-radio-group
              ><el-input
                v-else
                v-model="publishedResponses[Number(index)]"
                type="textarea"
                :rows="2"
                placeholder="请输入答案"
              />
            </article>
            <el-button type="primary" @click="submitPublishedBank"
              >提交本次答案</el-button
            ><el-result
              v-if="publishedGrade"
              :title="`本次正确率 ${publishedGrade.accuracy}%`"
            /></div></el-card
        ><el-card shadow="never" class="nested-card"
          ><template #header
            ><div class="card-header">
              <div>
                <b>AI 智能出题与作答</b>
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
              type="primary"
              :disabled="!blocks.length"
              @click="generateMemoryQuestions"
              >根据知识卡片生成</el-button
            ><label class="file-picker"
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
          <div v-else class="question-list">
            <article
              v-for="(item, index) in memoryQuestions"
              :key="index"
              class="question-item"
            >
              <b>{{ Number(index) + 1 }}. [{{ item.type }}] {{ item.question }}</b
              ><el-checkbox-group
                v-if="isMultiple(item)"
                v-model="memoryResponses[Number(index)]"
                ><el-checkbox
                  v-for="option in item.options || []"
                  :key="option"
                  :label="option"
                  >{{ option }}</el-checkbox
                ></el-checkbox-group
              ><el-radio-group
                v-else-if="isChoice(item)"
                v-model="memoryResponses[Number(index)]"
                ><el-radio
                  v-for="option in item.options || ['正确', '错误']"
                  :key="option"
                  :value="option"
                  >{{ option }}</el-radio
                ></el-radio-group
              ><el-input
                v-else
                v-model="memoryResponses[Number(index)]"
                type="textarea"
                :rows="3"
                placeholder="请输入简答内容"
              />
            </article>
            <el-button type="primary" @click="submitMemoryQuestions"
              >提交全部答案并由 AI 批改</el-button
            ><el-result
              v-if="memoryGrade"
              :title="`本次正确率 ${memoryGrade.score}%`"
              :sub-title="memoryGrade.summary"
            /></div></el-card
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
              >背诵平均 <b>{{ dashboard?.memory_average || 0 }}%</b></span
            ><span
              >练习平均 <b>{{ dashboard?.practice_average || 0 }}%</b></span
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
              <div
                v-for="point in dashboard?.weak_points || []"
                :key="point.point"
                class="weak-point"
              >
                <span>{{ point.point }}</span
                ><el-tag type="warning">{{ point.count }} 次</el-tag>
              </div>
            </div>
            <div>
              <h3>最近学习成绩</h3>
              <el-empty
                v-if="
                  !dashboard?.memory_attempts?.length &&
                  !dashboard?.practice_attempts?.length
                "
                description="暂无学习记录"
                :image-size="72"
              />
              <div
                v-for="attempt in [
                  ...(dashboard?.memory_attempts || []),
                  ...(dashboard?.practice_attempts || []),
                ].slice(0, 10)"
                :key="attempt.attempt_id"
                class="history-row"
              >
                <span>{{ attempt.mode || "AI练习" }}</span
                ><strong>{{ attempt.score }}%</strong
                ><small>{{ attempt.created_at }}</small>
              </div>
            </div>
          </div></el-card
        ><el-card shadow="never" class="nested-card"
          ><template #header><b>我的背诵本与错题本</b></template>
          <div class="export-actions">
            <el-button @click="exportBook('recitation_book_export')"
              >导出 Word 背诵本</el-button
            ><el-button @click="exportBook('wrong_question_book_export')"
              >导出 Word 错题本</el-button
            >
          </div>
          <el-empty
            v-if="
              !dashboard?.recitation_book?.length &&
              !dashboard?.wrong_question_book?.length
            "
            description="暂无错背或错题记录"
            :image-size="80"
          />
          <div
            v-for="item in dashboard?.recitation_book || []"
            :key="`recite-${item.attempt_id}`"
            class="record-card"
          >
            <b
              >{{ item.title || "知识块" }} · {{ item.mode }} ·
              {{ item.score }}%</b
            >
            <p>{{ item.feedback }}</p>
          </div>
          <div
            v-for="item in dashboard?.wrong_question_book || []"
            :key="`wrong-${item.created_at}-${item.question}`"
            class="record-card"
          >
            <b>[{{ item.type }}] {{ item.question }}</b>
            <p>
              正确答案：{{
                Array.isArray(item.correct_answer)
                  ? item.correct_answer.join("、")
                  : item.correct_answer
              }}
            </p>
            <small>{{ item.feedback }}</small>
          </div></el-card
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
            <KnowledgeGraphCanvas
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
    <AiSettingsDialog v-model="aiSettingsOpen" @changed="updateAiStatus" />
  </main>
</template>
