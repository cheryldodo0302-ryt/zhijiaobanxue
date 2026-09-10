import axios from 'axios'
export const api=axios.create({baseURL:'/api/v1',withCredentials:true,timeout:30000})
let accessToken=''
export const setAccessToken=(token:string)=>{accessToken=token}
api.interceptors.request.use(config=>{if(accessToken)config.headers.Authorization=`Bearer ${accessToken}`;return config})
api.interceptors.response.use(
  response=>response,
  error=>{
    const requestPath=String(error.config?.url||'')
    const isAuthAttempt=requestPath.endsWith('/auth/login')||requestPath.endsWith('/auth/refresh')
    if(error.response?.status===401&&!isAuthAttempt){
      setAccessToken('')
      if(window.location.pathname!=='/login')window.location.assign('/login')
    }
    return Promise.reject(error)
  },
)
