<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Reading } from '@element-plus/icons-vue'

const props = defineProps<{
  mode: string
  courseName: string
  documents: number
  blocks: number
  preparing: boolean
  loading: boolean
}>()
defineEmits<{ resume: []; review: []; profile: [] }>()
const modes: Record<string, { title: string; detail: string; label: string }> = {
  qa: { title: '从一个问题，走向真正理解。', detail: '提出疑问，跟随课程资料中的线索，一步步形成自己的答案。', label: '学习问答' },
  materials: { title: '把学习材料，整理成自己的知识。', detail: '查看课程资料与解析状态，为接下来的提问和复习做好准备。', label: '课程与材料' },
  blocks: { title: '让零散知识，形成清楚的脉络。', detail: '整理知识卡片，找到需要理解和反复记忆的关键内容。', label: '知识卡片' },
  training: { title: '再回想一次，让知识留下来。', detail: '通过挖空与复述检验记忆，把不熟悉的地方练得更扎实。', label: '训练巩固' },
  practice: { title: '用一次作答，检验这一段学习。', detail: '完成练习或课程试卷，在反馈中发现下一步要巩固的内容。', label: '作答与测验' },
  profile: { title: '看见积累，也找到下一步。', detail: '回顾当前课程的学习记录、成绩与薄弱知识点。', label: '我的学习' },
  graph: { title: '连接知识点，看见课程全貌。', detail: '沿着教师发布的知识关系，探索概念之间的联系。', label: '知识图谱' },
}
const current = computed(() => modes[props.mode] || modes.qa!)
</script>

<template>
  <section class="learning-focus" aria-label="继续当前课程学习">
    <div class="focus-message">
      <Transition name="focus-copy" mode="out-in">
        <div :key="mode" class="focus-copy">
          <h2>{{ current.title }}</h2>
          <p>{{ current.detail }}</p>
        </div>
      </Transition>
      <div class="focus-actions">
        <el-button type="primary" size="large" :disabled="loading" @click="$emit('resume')">
          继续学习 <el-icon class="forward-icon"><ArrowRight /></el-icon>
        </el-button>
        <el-button size="large" :loading="preparing" :disabled="loading" @click="$emit('review')">
          <el-icon><Reading /></el-icon>一键准备并复习
        </el-button>
      </div>
    </div>
    <div class="focus-context">
      <span class="context-label">当前学习位置</span>
      <strong class="context-course" :title="courseName">{{ courseName }}</strong>
      <Transition name="focus-mode" mode="out-in">
        <span :key="mode" class="context-mode">{{ current.label }}</span>
      </Transition>
      <div class="context-resources"><span>{{ documents }} 份资料</span><span>{{ blocks }} 张知识卡片</span></div>
      <button class="profile-link" @click="$emit('profile')">查看学习记录与成绩 <el-icon><ArrowRight /></el-icon></button>
    </div>
  </section>
</template>

<style scoped>
.learning-focus{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:48px;padding:32px 36px;border:1px solid #d8e5df;border-radius:16px;background:linear-gradient(115deg,#edf5f0 0%,#f8faf7 64%,#eff5f2 100%);color:#203e35;overflow:hidden}
.focus-message{display:flex;flex-direction:column;justify-content:center;min-width:0}.focus-copy{min-height:94px}.focus-copy h2{font-size:clamp(23px,2.1vw,31px);line-height:1.45;font-weight:650;letter-spacing:-.025em;margin:0 0 10px}.focus-copy p{font-size:14px;line-height:1.8;color:#56634d;margin:0;max-width:60ch}.focus-actions{display:flex;align-items:center;gap:12px;margin-top:24px}.focus-actions .el-button{margin:0;border-radius:9px;font-weight:600;gap:8px}.focus-actions :deep(.el-button>span){gap:8px}.forward-icon{margin-left:10px}.focus-context{border-left:1px solid #cddfd5;padding-left:28px;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;min-width:0}.context-label{font-size:12px;color:#56634d}.context-course{font-size:17px;line-height:1.6;margin-top:8px;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.context-mode{display:inline-block;margin-top:8px;color:#294b3c;font-size:14px;font-weight:600}.context-resources{display:flex;gap:16px;margin-top:18px;font-size:12px;color:#56634d}.profile-link{font:inherit;font-size:13px;display:flex;gap:10px;align-items:center;margin-top:14px;padding:4px 0;color:#245c4f;border:0;background:none;cursor:pointer}.profile-link:focus-visible{outline:2px solid #294b3c;outline-offset:4px;border-radius:3px}
@media(prefers-reduced-motion:no-preference){.learning-focus{animation:focus-arrive .65s cubic-bezier(.16,1,.3,1) both}.focus-context{animation:context-arrive .7s .1s cubic-bezier(.16,1,.3,1) both}.focus-copy-enter-active,.focus-copy-leave-active,.focus-mode-enter-active,.focus-mode-leave-active{transition:opacity .18s ease,transform .28s cubic-bezier(.16,1,.3,1)}.focus-copy-enter-from,.focus-mode-enter-from{opacity:0;transform:translateY(10px)}.focus-copy-leave-to,.focus-mode-leave-to{opacity:0;transform:translateY(-6px)}.focus-actions .el-button{transition:transform .22s cubic-bezier(.16,1,.3,1),background-color .2s,border-color .2s}.focus-actions .el-button:hover{transform:translateY(-2px)}.focus-actions .el-button:active{transform:translateY(0) scale(.98)}.forward-icon,.profile-link .el-icon{transition:transform .25s cubic-bezier(.16,1,.3,1)}.focus-actions .el-button:hover .forward-icon,.profile-link:hover .el-icon{transform:translateX(4px)}}
@keyframes focus-arrive{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:translateY(0)}}@keyframes context-arrive{from{opacity:0;transform:translateX(14px)}to{opacity:1;transform:translateX(0)}}
@media(max-width:1100px){.learning-focus{grid-template-columns:minmax(0,1fr) 220px;gap:24px;padding:26px}.focus-context{padding-left:22px}.focus-copy{min-height:114px}}
@media(max-width:760px){.learning-focus{grid-template-columns:1fr}.focus-context{border-left:0;border-top:1px solid #cddfd5;padding:20px 0 0}.focus-actions{flex-wrap:wrap}.focus-copy{min-height:0}}

/* The A3 footer is a pair of functional panels, not a promotional banner. */
.learning-focus{grid-template-columns:minmax(0,1.6fr) minmax(220px,1fr);padding:0;gap:16px;border:0;border-radius:0;background:transparent;overflow:visible}
.focus-message,.focus-context{padding:24px;border:1px solid #dce1d4;border-radius:10px;background:#fcfcf8;min-width:0}.focus-copy{min-height:0}.focus-copy h2{font-size:18px;line-height:1.5;margin-bottom:8px}.focus-copy p{font-size:13px}.focus-actions{margin-top:18px;gap:10px;flex-wrap:wrap}.focus-actions .el-button{font-size:13px;height:36px}.focus-context{padding-left:24px}.context-course{font-size:15px;margin-top:6px;white-space:normal}.context-resources{margin-top:12px}.profile-link{font-size:12px}.context-mode{font-size:12px}.focus-context,.learning-focus{animation:none}
@media(max-width:760px){.learning-focus{grid-template-columns:minmax(0,1fr)}.focus-message,.focus-context{padding:20px}.focus-context{border:1px solid #dce1d4}.focus-copy{min-height:0}}
</style>
