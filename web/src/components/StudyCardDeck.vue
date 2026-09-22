<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { vCardHover } from '../card-hover'
import { ArrowLeft, ArrowRight, RefreshRight } from '@element-plus/icons-vue'
import KnowledgeMarkdown from './KnowledgeMarkdown.vue'
import StudyArtwork from './StudyArtwork.vue'

const props = defineProps<{ cards: { block_id: number | string; title: string; content: string; keywords?: string[] }[]; courseId: string }>()
const index = ref(0), revealed = ref(false), direction = ref('next')
const current = computed(() => props.cards[index.value])
const stackCards = computed(() => {
  const start = Math.max(0, index.value - 2)
  const end = Math.min(props.cards.length, index.value + 3)
  return props.cards.slice(start, end).map((card, position) => ({ card, offset: start + position - index.value })).filter(item => item.offset !== 0)
})
watch(() => props.courseId, () => { index.value = 0; revealed.value = false })
watch(() => props.cards, () => { index.value = Math.min(index.value, Math.max(0, props.cards.length - 1)); revealed.value = false })
function move(offset: number) {
  const next = index.value + offset
  if (next < 0 || next >= props.cards.length) return
  direction.value = offset > 0 ? 'next' : 'previous'
  revealed.value = false
  index.value = next
}
function stackCardStyle(offset: number) {
  return { zIndex: 3 - Math.abs(offset) }
}
</script>

<template>
  <section v-if="current" class="study-deck" aria-label="知识卡片逐张复习">
    <div class="deck-heading"><div><h3>回想一下，再看答案</h3><p>先在心里解释这个知识点，再翻面核对。</p></div><span aria-live="polite">{{ index + 1 }} / {{ cards.length }}</span></div>
    <div class="deck-stage">
      <button v-card-hover="() => move(entry.offset)" v-for="entry in stackCards" :key="String(entry.card.block_id)" type="button" :class="['deck-stack-card',entry.offset < 0 ? 'deck-stack-prev' : 'deck-stack-next',`deck-stack-depth-${Math.abs(entry.offset)}`]" :style="stackCardStyle(entry.offset)" :aria-label="`切换到${entry.offset < 0 ? '前' : '后'}${Math.abs(entry.offset)}张：${entry.card.title}`" @click="move(entry.offset)">
        <span>{{ entry.offset < 0 ? `前${Math.abs(entry.offset)}张` : `后${entry.offset}张` }}</span><strong>{{ entry.card.title }}</strong>
      </button>
      <Transition :name="`deck-${direction}`">
        <article :key="String(current.block_id)" class="deck-card" :class="{ revealed }">
          <div v-if="!revealed" class="deck-front"><StudyArtwork/><span>知识回想</span><h4>{{ current.title }}</h4><p>你能用自己的话说明它吗？</p></div>
          <div v-else class="deck-answer"><h4>{{ current.title }}</h4><KnowledgeMarkdown :content="current.content"/></div>
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
.study-deck{margin-bottom:26px;min-width:0}
.deck-heading{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px}
.deck-heading h3{font-size:17px;margin:0 0 6px}
.deck-heading p{font-size:13px;color:#56634d;margin:0}
.deck-heading>span{white-space:nowrap;color:#56634d}
.deck-stage{position:relative;display:grid;grid-template-columns:minmax(0,1fr);align-items:center;justify-items:center;min-height:320px;padding:24px 0 48px;overflow:hidden;isolation:isolate}
.deck-card,.deck-stack-card{grid-area:1/1;width:58%;min-width:0;box-sizing:border-box;border:1px solid #cfdfd8;border-radius:12px;background:#f7faf7;overflow:hidden}
.deck-stack-card{min-height:240px;padding:26px 20px;color:#56634d;text-align:left;cursor:pointer;font:inherit;transform-origin:center 80%;box-shadow:0 8px 18px rgba(41,75,60,.06)}
.deck-stack-card span{display:block;margin-bottom:12px;font-size:12px}
.deck-stack-card strong{display:block;color:#294b3c;font-size:16px;line-height:1.5;overflow-wrap:anywhere}
.deck-stack-prev.deck-stack-depth-1{transform:translate(-35%,14px) rotate(-7deg) scale(.9)}
.deck-stack-prev.deck-stack-depth-2{transform:translate(-65%,30px) rotate(-13deg) scale(.8)}
.deck-stack-next.deck-stack-depth-1{transform:translate(35%,14px) rotate(7deg) scale(.9)}
.deck-stack-next.deck-stack-depth-2{transform:translate(65%,30px) rotate(13deg) scale(.8)}
.deck-stack-card:focus-visible{outline:3px solid #294b3c;outline-offset:3px}
.deck-card{z-index:4;min-height:240px;box-shadow:0 18px 40px rgba(41,75,60,.13)}
.deck-front{position:relative;min-height:240px;padding:36px 24px;overflow:hidden}
.deck-front>.study-artwork{position:absolute;right:0;bottom:0;opacity:.65;pointer-events:none}
.deck-front>span{font-size:12px;color:#56634d}
.deck-card h4{font-size:23px;font-weight:600;margin:16px 0;position:relative;overflow-wrap:anywhere}
.deck-front p{font-size:14px;color:#56634d;position:relative}
.deck-answer{padding:22px 24px;max-height:420px;overflow:auto;min-height:240px}
.deck-answer h4{font-size:18px}
.deck-controls{position:relative;z-index:10;display:flex;justify-content:center;gap:12px;margin-top:18px}
.deck-controls .el-button{margin:0}
.deck-controls .el-icon{margin-left:6px}
.deck-next-leave-active,.deck-previous-leave-active{pointer-events:none}
@media(prefers-reduced-motion:no-preference){
.deck-stack-card{transition:transform .6s ease-in-out}
.deck-next-enter-active,.deck-next-leave-active,.deck-previous-enter-active,.deck-previous-leave-active{transition:transform .6s ease-in-out,opacity .6s ease-in-out}
.deck-next-enter-from,.deck-previous-leave-to{opacity:0;transform:translate(35%,14px) rotate(7deg) scale(.9)}
.deck-next-leave-to,.deck-previous-enter-from{opacity:0;transform:translate(-35%,14px) rotate(-7deg) scale(.9)}
}
@media(max-width:600px){.deck-stage{padding:12px 0;min-height:240px}.deck-stack-card{display:none}.deck-card{width:100%}.deck-controls{gap:6px}.deck-controls .el-button{padding:8px 10px}.deck-card h4{font-size:20px}}
@media (hover:hover) and (pointer:fine) {
  .deck-stack-card.card-hovered { z-index:6!important; box-shadow:0 24px 44px rgba(41,75,60,.2); }
  .deck-stack-prev.deck-stack-depth-1.card-hovered { transform:translate3d(-35%,2px,26px) rotate(-7deg) scale(.98) rotateX(var(--hover-rotate-x,0deg)) rotateY(var(--hover-rotate-y,0deg)); }
  .deck-stack-prev.deck-stack-depth-2.card-hovered { transform:translate3d(-65%,14px,18px) rotate(-13deg) scale(.88) rotateX(var(--hover-rotate-x,0deg)) rotateY(var(--hover-rotate-y,0deg)); }
  .deck-stack-next.deck-stack-depth-1.card-hovered { transform:translate3d(35%,2px,26px) rotate(7deg) scale(.98) rotateX(var(--hover-rotate-x,0deg)) rotateY(var(--hover-rotate-y,0deg)); }
  .deck-stack-next.deck-stack-depth-2.card-hovered { transform:translate3d(65%,14px,18px) rotate(13deg) scale(.88) rotateX(var(--hover-rotate-x,0deg)) rotateY(var(--hover-rotate-y,0deg)); }
  .deck-card:hover { transform:translate3d(0,-4px,0) scale(1.025); }
}
@media (prefers-reduced-motion:no-preference) {
  .deck-stack-card,.deck-card { transition:transform .72s cubic-bezier(.22,1,.36,1),opacity .72s ease,box-shadow .24s ease; will-change:transform; }
}
@media (prefers-reduced-motion:reduce) {
  .deck-stack-card,.deck-card { transition:none; }
  .deck-stack-card.card-hovered,.deck-card:hover { transform:none; }
}
@media (prefers-reduced-motion:no-preference) {
  .deck-stage{perspective:1200px;transform-style:preserve-3d}
  .deck-next-enter-active,.deck-next-leave-active,.deck-previous-enter-active,.deck-previous-leave-active{transition:transform .72s cubic-bezier(.22,1,.36,1),opacity .48s ease,box-shadow .24s ease}
  .deck-next-enter-from{opacity:.5;transform:translate3d(35%,18px,20px) rotateY(-14deg) rotateZ(7deg) scale(.9)}
  .deck-next-leave-to{opacity:.08;transform:translate3d(-35%,18px,-10px) rotateY(14deg) rotateZ(-7deg) scale(.9)}
  .deck-previous-enter-from{opacity:.5;transform:translate3d(-35%,18px,20px) rotateY(14deg) rotateZ(-7deg) scale(.9)}
  .deck-previous-leave-to{opacity:.08;transform:translate3d(35%,18px,-10px) rotateY(-14deg) rotateZ(7deg) scale(.9)}
}
</style>
