import { afterEach, expect, it, vi } from 'vitest'
import { AxiosError } from 'axios'
import { api, setAccessToken } from './api'

const originalAdapter = api.defaults.adapter
afterEach(() => { api.defaults.adapter = originalAdapter; setAccessToken(''); vi.unstubAllGlobals() })
const reply = (config:any, data:any) => ({ data, status:200, statusText:'OK', headers:{}, config })
const unauthorized = (config:any) => new AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined,
  { data:{}, status:401, statusText:'Unauthorized', headers:{}, config })

it('refreshes once for concurrent expired requests and replays each once', async () => {
  setAccessToken('expired')
  let refreshes = 0, writes = 0
  api.defaults.adapter = async config => {
    if (config.url === '/auth/refresh') {
      refreshes++
      await new Promise(resolve => setTimeout(resolve, 10))
      return reply(config, { access_token:'renewed' })
    }
    if (config.headers.Authorization !== 'Bearer renewed') throw unauthorized(config)
    writes++
    return reply(config, { ok:true })
  }
  const results = await Promise.all([api.post('/operation-a'), api.post('/operation-b')])
  expect(results.every(result => result.data.ok)).toBe(true)
  expect(refreshes).toBe(1)
  expect(writes).toBe(2)
})

it('never replays a write on timeout and stops after a failed refresh', async () => {
  setAccessToken('expired')
  let calls = 0
  api.defaults.adapter = async config => { calls++; throw new AxiosError('timeout', 'ECONNABORTED', config) }
  await expect(api.post('/submit')).rejects.toThrow('timeout')
  expect(calls).toBe(1)
  const assign = vi.fn()
  vi.stubGlobal('window', { location:{ pathname:'/student/courses', search:'?course=b', assign } })
  calls = 0
  api.defaults.adapter = async config => { calls++; throw unauthorized(config) }
  await expect(api.post('/submit')).rejects.toThrow('expired')
  expect(calls).toBe(2)
  expect(assign).toHaveBeenCalledOnce()
})

it('does not replay an old account write after switching accounts', async () => {
  setAccessToken('account-a')
  let release!: () => void
  let started!: () => void
  const ready = new Promise<void>(resolve => { started = resolve })
  let calls = 0
  api.defaults.adapter = async config => {
    calls++
    if (config.url === '/submit') {
      await new Promise<void>(resolve => { release = resolve; started() })
      throw unauthorized(config)
    }
    return reply(config, { token:config.headers.Authorization })
  }
  const result = expect(api.post('/submit')).rejects.toThrow('expired')
  await ready
  setAccessToken('account-b')
  release()
  await result
  expect(calls).toBe(1)
  expect((await api.get('/current')).data.token).toBe('Bearer account-b')
})

it.each([false, true])('ignores an old account refresh after switching (rejected=%s)', async rejected => {
  setAccessToken('account-a')
  const assign = vi.fn()
  vi.stubGlobal('window', { location:{ pathname:'/student/courses', search:'', assign } })
  let release!: () => void
  let started!: () => void
  const ready = new Promise<void>(resolve => { started = resolve })
  let writes = 0
  api.defaults.adapter = async config => {
    if (config.url === '/auth/refresh') {
      await new Promise<void>(resolve => { release = resolve; started() })
      if (rejected) throw unauthorized(config)
      return reply(config, { access_token:'renewed-a' })
    }
    if (config.url === '/submit') { writes++; throw unauthorized(config) }
    return reply(config, { token:config.headers.Authorization })
  }
  const result = expect(api.post('/submit')).rejects.toThrow()
  await ready
  setAccessToken('account-b')
  release()
  await result
  expect(writes).toBe(1)
  expect(assign).not.toHaveBeenCalled()
  expect((await api.get('/current')).data.token).toBe('Bearer account-b')
})
