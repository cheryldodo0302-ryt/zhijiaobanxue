<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { ArrowLeft, ArrowRight, Flag } from '@element-plus/icons-vue'
import KnowledgeMarkdown from './KnowledgeMarkdown.vue'
import { questionTypeLabel } from '../question-display'

const props = defineProps<{
  title: string
  subtitle?: string
  items: any[]
  answered: boolean[]
  disabled?: boolean
}>()
const root = ref<HTMLElement | null>(null)
const mode = ref('all'), current = ref(0), marked = ref(new Set<number>())
const uid = useId()
const complete = computed(() => props.items.filter((_, i) => props.answered[i]).length)
const remaining = computed(() => props.items.length - complete.value)
let observer: IntersectionObserver | undefined
let animation: Animation | undefined
function reduced() { return window.matchMedia('(prefers-reduced-motion: reduce)').matches }
async function jump(index: number) {
  if (index < 0 || index >= props.items.length) return
  current.value = index
  await nextTick()
  const article = root.value?.querySelector<HTMLElement>(`[data-question-index="${index}"]`)
  article?.scrollIntoView({ behavior: reduced() ? 'instant' : 'smooth', block: 'start' })
  article?.focus({ preventScroll: true })
  animation?.cancel()
  if (article && !reduced()) animation = article.animate([{opacity:.55,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}], {duration:320,easing:'cubic-bezier(.16,1,.3,1)'})
}
function toggleMark(index: number) {
  const next = new Set(marked.value)
  next.has(index) ? next.delete(index) : next.add(index)
  marked.value = next
}
async function observe() {
  observer?.disconnect()
  await nextTick()
  if (mode.value !== 'all' || !root.value || typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)
    if (visible[0]) current.value = Number((visible[0].target as HTMLElement).dataset.questionIndex)
  }, {rootMargin:'-64px 0px -55% 0px',threshold:0})
  root.value.querySelectorAll('[data-question-index]').forEach(element => observer!.observe(element))
}
watch(mode, async () => { await observe(); await jump(current.value) })
watch(() => props.items, () => { current.value=0;marked.value=new Set();void observe() }, {immediate:true})
onBeforeUnmount(() => {observer?.disconnect();animation?.cancel()})
</script>

<template>
  <section ref="root" class="paper-workspace" :aria-label="title">
    <header class="paper-heading">
      <div><h2>{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div>
      <el-radio-group v-model="mode" aria-label="试卷浏览方式" size="large">
        <el-radio-button value="all">整卷浏览</el-radio-button>
        <el-radio-button value="single">逐题作答</el-radio-button>
      </el-radio-group>
    </header>
    <div class="paper-layout">
      <div class="paper-questions">
        <el-empty v-if="!items.length" description="这份试卷暂无题目" />
        <article v-for="(item,index) in items" v-show="mode==='all'||current===index" :key="item.item_id ?? index"
          :data-question-index="index" :aria-labelledby="`${uid}-question-${index}`" tabindex="-1"
          class="paper-question" :class="{'is-current':current===index}" @focusin="current=index">
          <header class="question-heading">
            <div class="question-identity"><span class="question-number">{{ String(index+1).padStart(2,'0') }}</span>
              <h3 :id="`${uid}-question-${index}`">{{ questionTypeLabel(item.question_type || item.type) }}</h3>
              <span v-if="item.points != null" class="question-points">{{ item.points }} 分</span>
            </div>
            <button class="mark-button" :class="{'is-marked':marked.has(index)}" :aria-pressed="marked.has(index)" :aria-label="`${marked.has(index)?'取消标记':'标记'}第 ${index+1} 题`" @click="toggleMark(index)">
              <el-icon><Flag /></el-icon>{{ marked.has(index) ? '已标记' : '稍后检查' }}
            </button>
          </header>
          <KnowledgeMarkdown class="question-stem" :content="String(item.stem ?? item.question ?? item.stem_markdown ?? '')" />
          <fieldset class="question-answer" :disabled="disabled"><legend class="visually-hidden">第 {{index+1}} 题答案</legend><slot name="answer" :item="item" :index="index" /></fieldset>
          <footer class="question-footer"><span :class="{'answer-complete':answered[index]}">{{ answered[index] ? '已作答' : '未作答' }}</span><span>第 {{index+1}} 题 / 共 {{items.length}} 题</span></footer>
        </article>
        <div v-if="mode==='single'&&items.length" class="question-paging"><el-button :disabled="current===0" :icon="ArrowLeft" @click="jump(current-1)">上一题</el-button><span>{{current+1}} / {{items.length}}</span><el-button :disabled="current===items.length-1" @click="jump(current+1)">下一题<el-icon><ArrowRight /></el-icon></el-button></div>
        <slot name="result" />
      </div>
      <aside class="paper-answer-sheet" aria-label="答题卡">
        <div class="answer-sheet-heading"><h3>答题卡</h3><span>{{complete}} / {{items.length}}</span></div>
        <p class="sheet-summary">{{remaining ? `还有 ${remaining} 题未作答` : items.length ? '全部已作答，可以检查并提交' : '等待载入题目'}}</p>
        <nav class="answer-grid" aria-label="题号导航">
          <button v-for="(_,index) in items" :key="index" :class="{'is-answered':answered[index],'is-current':current===index,'is-marked':marked.has(index)}"
            :aria-label="`第 ${index+1} 题，${answered[index]?'已作答':'未作答'}${marked.has(index)?'，已标记':''}`" :aria-current="current===index?'step':undefined" @click="jump(index)">{{index+1}}<span v-if="marked.has(index)" class="marked-corner" /></button>
        </nav>
        <div class="sheet-legend"><span><i class="legend-done" />已作答</span><span><i />未作答</span><span><i class="legend-mark" />已标记</span></div>
        <el-button v-if="remaining && complete" text class="first-unanswered" @click="jump(items.findIndex((_,i)=>!answered[i]))">定位首道未答题<el-icon><ArrowRight /></el-icon></el-button>
        <div class="sheet-submit"><slot name="submit" /></div>
        <slot name="notice" />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.paper-workspace{--paper-accent:#23746f;--paper-border:#dce5df;min-width:0}.paper-heading{display:flex;align-items:center;justify-content:space-between;gap:24px;margin:8px 0 24px}.paper-heading h2{font-size:25px;font-weight:650;line-height:1.45;margin:0;color:#203c34}.paper-heading p{font-size:13px;color:#52695f;line-height:1.7;margin:8px 0 0}.paper-heading .el-radio-group{flex-shrink:0}.paper-layout{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:24px;align-items:start}.paper-questions{min-width:0;display:grid;gap:20px}.paper-question{scroll-margin-top:24px;padding:26px 30px 18px;background:#fff;border:1px solid var(--paper-border);border-radius:14px;min-width:0}.paper-question.is-current{border-color:#8cb4a8}.paper-question:focus-visible{outline:2px solid var(--paper-accent);outline-offset:3px}.question-heading,.question-identity,.question-footer,.answer-sheet-heading,.question-paging{display:flex;align-items:center;justify-content:space-between;gap:12px}.question-identity{justify-content:flex-start}.question-number{display:grid;place-items:center;min-width:38px;height:38px;padding:0 6px;background:#edf5f0;border-radius:9px;font-size:17px;font-weight:650;color:var(--paper-accent);font-variant-numeric:tabular-nums}.question-identity h3{font-size:14px;margin:0;font-weight:600}.question-points{font-size:12px;color:#52695f}.mark-button{display:flex;align-items:center;gap:6px;padding:8px 0 8px 8px;background:none;border:0;color:#64736e;font:inherit;font-size:12px;cursor:pointer}.mark-button.is-marked{color:#946312}.mark-button:focus-visible{outline:2px solid var(--paper-accent);outline-offset:3px}.question-stem{font-size:16px;margin:18px 0 22px;color:#253f37;line-height:1.85}.question-answer{padding:0;margin:0;border:0;min-width:0}.question-answer :deep(.el-radio-group),.question-answer :deep(.el-checkbox-group){display:grid;width:100%;gap:10px}.question-answer :deep(.el-radio),.question-answer :deep(.el-checkbox){display:flex;align-items:flex-start;height:auto;min-height:48px;margin:0!important;padding:14px 16px;border:1px solid #e2e8e3;border-radius:9px;background:#fafcf9;white-space:normal}.question-answer :deep(.el-radio.is-checked),.question-answer :deep(.el-checkbox.is-checked){background:#edf7f2;border-color:#85b3a6}.question-answer :deep(.el-radio__input),.question-answer :deep(.el-checkbox__input){margin-top:3px}.question-answer :deep(.el-radio__label),.question-answer :deep(.el-checkbox__label){font-size:14px;line-height:1.65;white-space:normal;overflow-wrap:anywhere;min-width:0;color:#324d42}.question-answer :deep(.el-radio.is-disabled),.question-answer :deep(.el-checkbox.is-disabled){cursor:default}.question-footer{border-top:1px solid #edf0ec;margin-top:24px;padding-top:14px;font-size:12px;color:#64736e}.answer-complete{color:var(--paper-accent);font-weight:600}.paper-answer-sheet{position:sticky;top:24px;background:#fff;border:1px solid var(--paper-border);border-radius:14px;padding:22px;min-width:0}.answer-sheet-heading h3{font-size:17px;margin:0}.answer-sheet-heading>span{font-size:14px;color:var(--paper-accent);font-variant-numeric:tabular-nums}.sheet-summary{font-size:12px;color:#52695f;line-height:1.7;margin:10px 0 20px}.answer-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;max-height:clamp(120px,40dvh,420px);overflow-y:auto;padding:3px}.answer-grid button{position:relative;min-width:0;height:34px;background:#f8faf7;border:1px solid #dce5df;border-radius:7px;font:inherit;font-size:13px;color:#52695f;cursor:pointer;overflow:hidden}.answer-grid button.is-answered{background:#e0f0e7;border-color:#bad8c9;color:#245c4f}.answer-grid button.is-current{background:var(--paper-accent);border-color:var(--paper-accent);color:#fff}.answer-grid button:focus-visible{outline:2px solid #173e49;outline-offset:2px}.marked-corner{position:absolute;right:0;top:0;width:7px;height:7px;background:#c88b24;border-bottom-left-radius:4px}.sheet-legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px;font-size:11px;color:#52695f}.sheet-legend>span{display:flex;gap:5px;align-items:center}.sheet-legend i{width:9px;height:9px;border:1px solid #cbd8ce;border-radius:2px;background:#f8faf7}.sheet-legend .legend-done{background:#e0f0e7}.sheet-legend .legend-mark{background:#c88b24;border-color:#c88b24}.first-unanswered{margin-top:14px!important;padding-inline:0}.sheet-submit{margin-top:22px;padding-top:18px;border-top:1px solid #e5ece7}.sheet-submit :deep(.el-button){width:100%;min-height:42px;margin:0}.paper-answer-sheet :deep(.paper-notice){font-size:12px;line-height:1.7;color:#64736e;margin:12px 0 0}.question-paging{padding:6px 0;font-size:13px;color:#52695f}.question-paging :deep(.el-icon){margin-left:6px}.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(prefers-reduced-motion:no-preference){.paper-question{animation:paper-enter .35s cubic-bezier(.16,1,.3,1);transition:border-color .2s}.answer-grid button,.question-answer :deep(.el-radio),.question-answer :deep(.el-checkbox){transition:background-color .18s,border-color .18s,transform .2s cubic-bezier(.16,1,.3,1)}.answer-grid button:hover{transform:translateY(-2px)}.answer-grid button:active{transform:scale(.94)}.question-answer :deep(.el-radio:hover:not(.is-disabled)),.question-answer :deep(.el-checkbox:hover:not(.is-disabled)){border-color:#8cb4a8}}
@keyframes paper-enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1100px){.paper-layout{grid-template-columns:minmax(0,1fr) 220px;gap:18px}.paper-question{padding:22px}.paper-answer-sheet{padding:16px}.paper-heading{align-items:flex-start}.paper-heading h2{font-size:22px}}
@media(max-width:760px){.paper-layout{grid-template-columns:1fr}.paper-answer-sheet{position:static}.paper-heading{flex-direction:column}.paper-question{padding:18px}}
</style>
