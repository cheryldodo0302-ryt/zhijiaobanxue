import axios from 'axios'

export const api = axios.create({ baseURL:'/api/v1', withCredentials:true, timeout:30000 })
let accessToken = ''
let sessionVersion = 0
let refreshPending: { version:number; promise:Promise<void> } | null = null
export const setAccessToken = (token:string) => { accessToken = token; sessionVersion++ }

function loginRequired() {
  setAccessToken('')
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    const returnTo = window.location.pathname + window.location.search
    window.location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`)
  }
}

api.interceptors.request.use(config => {
  const request = config as typeof config & { _sessionVersion?:number }
  if (request._sessionVersion !== undefined && request._sessionVersion !== sessionVersion) {
    throw new Error('登录状态已变更')
  }
  request._sessionVersion = sessionVersion
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  else delete config.headers.Authorization
  return config
})
api.interceptors.response.use(response => response, async error => {
  const request = error.config
  if (error.response?.status !== 401 || !request || String(request.url).includes('/auth/')) throw error
  if (request._sessionVersion !== sessionVersion) throw error
  if (request._sessionRetried) { loginRequired(); throw error }
  request._sessionRetried = true
  // A sibling request may already have refreshed this expired access token.
  if (!accessToken || request.headers?.Authorization === `Bearer ${accessToken}`) {
    if (!refreshPending || refreshPending.version !== sessionVersion) {
      const pending = { version:sessionVersion, promise:Promise.resolve() }
      pending.promise = api.post('/auth/refresh').then(({ data }) => {
        if (pending.version !== sessionVersion) throw new Error('登录状态已变更')
        // Refresh preserves the login session; explicit login/logout changes it.
        accessToken = data.access_token
      }).finally(() => { if (refreshPending === pending) refreshPending = null })
      refreshPending = pending
    }
    try { await refreshPending.promise }
    catch (refreshError:any) {
      if (request._sessionVersion === sessionVersion && refreshError.response?.status === 401) loginRequired()
      throw refreshError
    }
  }
  if (request._sessionVersion !== sessionVersion) throw new Error('登录状态已变更')
  // Only replay a request rejected by authentication, never a network failure.
  return api.request(request)
})
