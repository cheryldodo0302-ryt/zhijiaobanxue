<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import editorialArtwork from '../assets/login-grove-hd.png'

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
async function submit() {
  if (loading.value || !form.username || !form.password) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(form.username, form.password)
    const requested = String(route.query.returnTo || '')
    const safeReturn = requested.startsWith('/') && !requested.startsWith('//') ? requested : ''
    await router.push(auth.user?.must_change_password ? '/change-password' : safeReturn || (auth.user?.role === 'student' ? '/student/courses' : '/'))
  } catch (e: any) {
    error.value = e.response?.data?.detail || (e.request ? '暂时无法连接服务，请稍后重试或联系维护人员' : '登录失败，请检查账号和密码')
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <main class="login-page">
    <div class="login-layout">
      <header class="login-header">
        <span class="login-wordmark">智教伴学</span>
        <span class="login-audience">学生与教师学习平台</span>
      </header>
      <div class="login-content">
        <section class="login-artwork" aria-labelledby="loginSlogan">
          <div class="login-poster">
            <img class="grove-back" :src="editorialArtwork" width="1536" height="1024" alt="" fetchpriority="high" decoding="async" />
            <h2 id="loginSlogan" class="login-slogan"><span class="slogan-first">让课程</span><span class="slogan-middle"><em>知识</em>成为</span><span class="slogan-last">可靠答案</span></h2>
            <img class="grove-front" :src="editorialArtwork" width="1536" height="1024" alt="" aria-hidden="true" />
          </div>
          <p class="login-art-caption">从课程资料出发，让学习有据可循。</p>
        </section>
      <section class="login-panel" aria-labelledby="loginTitle">
        <h1 id="loginTitle">账号登录</h1>
        <p class="login-intro">教师账号或学生学号均可登录</p>
        <el-form label-position="top" :aria-busy="loading" @submit.prevent="submit">
          <el-form-item label="用户名 / 学号" for="loginUsername">
            <el-input id="loginUsername" v-model="form.username" name="username" size="large" autocomplete="username" placeholder="请输入用户名或学号" :aria-describedby="error ? 'loginError' : undefined" />
          </el-form-item>
          <el-form-item label="密码" for="loginPassword">
            <el-input id="loginPassword" v-model="form.password" name="password" size="large" type="password" show-password autocomplete="current-password" placeholder="请输入密码" :aria-describedby="error ? 'loginError' : undefined" />
          </el-form-item>
          <el-button class="login-submit" type="primary" native-type="submit" size="large" :loading="loading" :disabled="loading || !form.username || !form.password">{{ loading ? '登录中…' : '登录' }}</el-button>
          <p v-if="error" id="loginError" class="login-error" role="alert">{{ error }}</p>
        </el-form>
      </section>
      </div>
      <footer class="login-footer">智教伴学系统</footer>
    </div>
  </main>
</template>
