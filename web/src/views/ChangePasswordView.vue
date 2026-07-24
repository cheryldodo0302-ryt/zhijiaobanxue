<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
const form=reactive({old_password:'',new_password:'',confirm:''}),loading=ref(false),auth=useAuthStore(),router=useRouter()
async function submit(){if(form.new_password!==form.confirm)return ElMessage.error('两次输入的新密码不一致');loading.value=true;try{await auth.changePassword(form.old_password,form.new_password);ElMessage.success('密码已修改');await router.replace(auth.user?.role==='teacher'?'/':'/student/courses')}catch(e:any){ElMessage.error(e.response?.data?.detail||'修改密码失败')}finally{loading.value=false}}
</script>
<template><main class="login-shell"><section class="login-brand"><span class="eyebrow">FIRST SIGN-IN</span><h1>首次登录<br/>请设置个人密码</h1><p>初始密码仅用于第一次登录。修改后，原有登录令牌会全部失效。</p></section><el-card class="login-card" shadow="never"><h2>修改初始密码</h2><p class="muted">新密码至少 10 个字符，且不能与初始密码相同。</p><el-form label-position="top"><el-form-item label="初始密码"><el-input v-model="form.old_password" type="password" show-password/></el-form-item><el-form-item label="新密码"><el-input v-model="form.new_password" type="password" show-password/></el-form-item><el-form-item label="确认新密码"><el-input v-model="form.confirm" type="password" show-password @keyup.enter="submit"/></el-form-item><el-button type="primary" :loading="loading" :disabled="!form.old_password||form.new_password.length<10||!form.confirm" @click="submit">保存并继续</el-button></el-form></el-card></main></template>
