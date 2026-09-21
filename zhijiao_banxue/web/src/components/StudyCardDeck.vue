<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, RefreshRight } from '@element-plus/icons-vue'
import KnowledgeMarkdown from './KnowledgeMarkdown.vue'
import StudyArtwork from './StudyArtwork.vue'

const props = defineProps<{ cards: { block_id: number | string; title: string; content: string; keywords?: string[] }[]; courseId: string }>()
const index = ref(0), revealed = ref(false), direction = ref('next')
const current = computed(() => props.cards[index.value])
watch(() => props.courseId, () => { index.value = 0; revealed.value = false })
watch(() => props.cards, () => { index.value = Math.min(index.value, Math.max(0, props.cards.length - 1)); revealed.value = false })
function move(offset: number) {
  const next = index.value + offset
  if (next < 0 || next >= props.cards.length) return
  direction.value = offset > 0 ? 'next' : 'previous'
  revealed.value = false
  index.value = next
}
</script>

<template>
  <section v-if="current" class="study-deck" aria-label="知识卡片逐张复习">
    <div class="deck-heading"><div><h3>回想一下，再看答案</h3><p>先在心里解释这个知识点，再翻面核对。</p></div><span aria-live="polite">{{ index + 1 }} / {{ cards.length }}</span></div>
    <div class="deck-stage">
      <Transition :name="`deck-${direction}`" mode="out-in">
        <article :key="String(current.block_id)" class="deck-card" :class="{ revealed }">
          <Transition name="deck-flip" mode="out-in">
            <div v-if="!revealed" key="question" class="deck-front"><StudyArtwork/><span>知识回想</span><h4>{{ current.title }}</h4><p>你能用自己的话说明它吗？</p></div>
            <div v-else key="answer" class="deck-answer"><h4>{{ current.title }}</h4><KnowledgeMarkdown :content="current.content"/></div>
          </Transition>
        </article>
      </Transition>
    </div>
    <div class="deck-controls">
      <el-button :disabled="index === 0" :icon="ArrowLeft" aria-label="上一张知识卡片" @click="move(-1)">上一张</el-button>
      <el-button type="primary" plain :icon="RefreshRight" :aria-expanded="revealed" @click="revealed = !revealed">{{ revealed ? '返回题面' : '翻面查看' }}</el-button>
      <el-button :disabled="index === cards.length - 1" aria-label="下一张知识卡片" @click="move(1)">下一张<el-icon><ArrowRight/></el-icon></el-button>
    </div>
  </section>
</template>

<style scoped>
.study-deck{margin-bottom:26px}.deck-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}.deck-heading h3{font-size:17px;margin:0 0 6px}.deck-heading p{font-size:13px;color:#56634d;margin:0}.deck-heading>span{white-space:nowrap;font-variant-numeric:tabular-nums;color:#56634d}.deck-stage{perspective:1000px}.deck-card{border:1px solid #cfdfd8;border-radius:12px;background:#f7faf7;overflow:hidden;min-height:240px}.deck-front{position:relative;min-height:240px;padding:46px 36px;overflow:hidden}.deck-front>.study-artwork{position:absolute;right:0;bottom:0;opacity:.65}.deck-front>span{font-size:12px;color:#56634d}.deck-card h4{font-size:23px;font-weight:600;margin:16px 0;position:relative;max-width:80%;overflow-wrap:anywhere}.deck-front p{font-size:14px;color:#56634d;position:relative}.deck-answer{padding:22px 30px;max-height:420px;overflow:auto;min-height:240px}.deck-answer h4{font-size:18px}.deck-controls{display:flex;justify-content:center;gap:12px;margin-top:18px}.deck-controls .el-button{margin:0}.deck-controls .el-icon{margin-left:6px}
@media(prefers-reduced-motion:no-preference){.deck-next-enter-active,.deck-next-leave-active,.deck-previous-enter-active,.deck-previous-leave-active{transition:transform .22s cubic-bezier(.16,1,.3,1),opacity .18s}.deck-next-enter-from,.deck-previous-leave-to{opacity:0;transform:translateX(32px) rotateY(-5deg)}.deck-next-leave-to,.deck-previous-enter-from{opacity:0;transform:translateX(-32px) rotateY(5deg)}.deck-flip-enter-active,.deck-flip-leave-active{transition:transform .18s ease,opacity .18s}.deck-flip-enter-from{opacity:0;transform:rotateY(-18deg)}.deck-flip-leave-to{opacity:0;transform:rotateY(18deg)}}
@media(max-width:600px){.deck-front{padding:32px 22px}.deck-card h4{font-size:20px}.deck-controls{gap:6px}.deck-controls .el-button{padding:8px 10px}.deck-heading{align-items:flex-start}.deck-answer{padding:20px}}
</style>
