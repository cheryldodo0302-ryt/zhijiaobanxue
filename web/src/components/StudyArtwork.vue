<script setup lang="ts">
import autumnForest from '../assets/autumn-forest-pattern.png'
</script>

<template>
  <div class="study-artwork" aria-hidden="true">
    <img :src="autumnForest" alt="" width="1536" height="1024" decoding="async" draggable="false" />
  </div>
</template>

<style scoped>
/* The approved autumn motif; multiply merges its white field into the light surface. */
.study-artwork{position:relative;width:176px;height:150px;overflow:hidden;pointer-events:none;mix-blend-mode:multiply}
img{position:absolute;left:-15%;top:-15%;width:130%;height:130%;max-width:none;object-fit:contain;user-select:none}
</style>
