<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
const props = withDefaults(defineProps<{items?: any[] | null; limit?: number; label?: string; resetKey?: string}>(), {limit:5,label:'内容'})
const expanded=ref(false), root=ref<HTMLElement|null>(null), contentId=useId()
const all=computed(()=>Array.isArray(props.items)?props.items:[])
const visible=computed(()=>expanded.value?all.value:all.value.slice(0,props.limit))
watch(()=>props.resetKey,()=>{expanded.value=false})
async function toggle(){
  const collapsing=expanded.value
  expanded.value=!expanded.value
  await nextTick()
  if(collapsing && root.value && root.value.getBoundingClientRect().bottom<0) root.value.scrollIntoView({block:'start',behavior:'instant'})
}
</script>
<template>
  <div ref="root" class="expandable-list">
    <Transition name="list-content" mode="out-in">
      <div :id="contentId" :key="expanded ? 'expanded' : 'collapsed'"><slot :items="visible" /></div>
    </Transition>
    <button v-if="all.length>limit" class="list-more" :aria-controls="contentId" :aria-expanded="expanded" :aria-label="`${expanded?'收起':'查看更多'}${label}`" @click="toggle">
      {{expanded?'收起':`查看更多（还有 ${all.length-limit} 条）`}}<el-icon aria-hidden="true" :class="{expanded}"><ArrowDown /></el-icon>
    </button>
  </div>
</template>
<style scoped>
.expandable-list{min-width:0}.list-more{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin:12px 0 0;padding:11px 14px;border:1px solid #dce5df;border-radius:8px;background:#f7faf7;color:#245c4f;cursor:pointer;font:inherit;font-size:13px}.list-more:hover{background:#edf5f0;border-color:#a5c4b6}.list-more:focus-visible{outline:2px solid #23746f;outline-offset:3px}.list-more .el-icon{display:inline-block}.list-more .expanded{transform:rotate(180deg)}
@media(prefers-reduced-motion:no-preference){.list-more{transition:background-color .18s,border-color .18s}.list-more .el-icon{transition:transform .2s cubic-bezier(.16,1,.3,1)}.list-content-enter-active,.list-content-leave-active{transition:opacity .2s cubic-bezier(.16,1,.3,1),transform .24s cubic-bezier(.16,1,.3,1)}.list-content-enter-from{opacity:0;transform:translateY(8px)}.list-content-leave-to{opacity:0;transform:translateY(-6px)}}
</style>
