<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean], changed: [settings: AiSettings] }>()

interface AiSettings {
  mode: 'mock' | 'relay' | 'custom' | 'qwen'
  provider: string
  base_url: string
  model: string
  configured: boolean
  has_api_key: boolean
}

const loading = ref(false)
const form = reactive({ mode:'mock', provider:'auto', base_url:'', model:'qwen-plus', api_key:'' })
const current = ref<AiSettings | null>(null)
const open = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })

async function load() {
  loading.value = true
  try {
    current.value = (await api.get('/runtime/ai-settings')).data
    form.mode = current.value?.mode === 'qwen' ? 'custom' : current.value?.mode || 'mock'
    form.provider = current.value?.mode === 'custom' ? current.value.provider : 'auto'
    form.base_url = current.value?.mode === 'custom' ? current.value.base_url : ''
    form.model = current.value?.mode === 'custom' ? current.value.model : 'qwen-plus'
    form.api_key = ''
  } catch (error:any) { ElMessage.error(error.response?.data?.detail || 'AI 服务设置加载失败') }
  finally { loading.value = false }
}

async function save() {
  loading.value = true
  try {
    const { data } = await api.put('/runtime/ai-settings', form)
    current.value = data
    emit('changed', data)
    ElMessage.success('AI 服务设置已保存')
    open.value = false
  } catch (error:any) { ElMessage.error(error.response?.data?.detail || 'AI 服务设置保存失败') }
  finally { loading.value = false }
}

watch(() => props.modelValue, value => { if (value) load() })
</script>

<template>
  <el-dialog v-model="open" title="AI 服务设置" width="min(620px, 94vw)" append-to-body>
    <div v-loading="loading" class="ai-settings-form">
      <el-alert title="设置保存在当前运行这套系统的电脑中，API Key 不会返回到浏览器。" type="info" :closable="false" />
      <el-form label-position="top">
        <el-form-item label="调用方式">
          <el-segmented v-model="form.mode" :options="[
            {label:'确定性 Mock',value:'mock'}, {label:'默认云端服务',value:'relay'}, {label:'自定义接口',value:'custom'},
          ]" block />
        </el-form-item>
        <p v-if="form.mode==='mock'" class="muted">不联网、不需要 Key；相同输入得到稳定结果，适合测试和离线使用。</p>
        <p v-else-if="form.mode==='relay'" class="muted">使用项目配置的云端中转，真实模型密钥不会下载到本机。</p>
        <template v-else>
          <el-form-item label="接口协议">
            <el-select v-model="form.provider">
              <el-option label="自动识别" value="auto" />
              <el-option label="OpenAI 兼容接口" value="openai_compatible" />
              <el-option label="Google Gemini 原生接口" value="gemini" />
              <el-option label="本机 Ollama" value="ollama" />
            </el-select>
          </el-form-item>
          <el-form-item label="API Base URL">
            <el-input v-model="form.base_url" placeholder="https://example.com/v1 或 http://127.0.0.1:11434/v1" />
          </el-form-item>
          <el-form-item label="模型名称"><el-input v-model="form.model" /></el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="form.api_key" type="password" show-password :placeholder="current?.has_api_key ? '已配置；留空表示继续使用原 Key' : 'Ollama 可留空'" autocomplete="new-password" />
          </el-form-item>
        </template>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="open=false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="save">保存并使用</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>.ai-settings-form{display:grid;gap:18px}.ai-settings-form .muted{margin:0 0 12px;line-height:1.7}</style>
