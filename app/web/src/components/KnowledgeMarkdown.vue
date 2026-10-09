<script setup lang="ts">
import {computed} from 'vue'
import {createKnowledgeMarkdown} from '../knowledge-markdown'
import 'katex/dist/katex.min.css'

const props = defineProps<{content: string}>()
const markdown = createKnowledgeMarkdown()
const html = computed(() => markdown.render(props.content || ''))
</script>

<template><div class="knowledge-markdown" v-html="html" /></template>

<style scoped>
.knowledge-markdown{min-width:0;max-width:100%;overflow-x:auto;overflow-wrap:anywhere;line-height:1.75;color:#36534c;text-align:left}
.knowledge-markdown :deep(h1){font-size:1.65em}
.knowledge-markdown :deep(h2){font-size:1.4em}
.knowledge-markdown :deep(h3){font-size:1.2em}
.knowledge-markdown :deep(h4),.knowledge-markdown :deep(h5),.knowledge-markdown :deep(h6){font-size:1.05em}
.knowledge-markdown :deep(h1),.knowledge-markdown :deep(h2),.knowledge-markdown :deep(h3),.knowledge-markdown :deep(h4),.knowledge-markdown :deep(h5),.knowledge-markdown :deep(h6){margin:1em 0 .5em;line-height:1.4;font-weight:700}
.knowledge-markdown :deep(p){margin:.65em 0;white-space:normal}
.knowledge-markdown :deep(ul),.knowledge-markdown :deep(ol){padding-left:1.6em}
.knowledge-markdown :deep(pre){overflow:auto;padding:12px;background:#f1f5f4;border-radius:8px;white-space:pre}
.knowledge-markdown :deep(blockquote){margin:12px 0;padding-left:14px;border-left:3px solid #9bc5bb}
.knowledge-markdown :deep(table){border-collapse:collapse;max-width:100%}
.knowledge-markdown :deep(th),.knowledge-markdown :deep(td){border:1px solid #dce8e5;padding:6px 10px}
.knowledge-markdown :deep(img){max-width:100%}
.knowledge-markdown :deep(.katex-display){overflow-x:auto;overflow-y:hidden;padding:6px 0}
</style>
