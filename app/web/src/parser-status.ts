export type ParserConnectionState = 'connected' | 'partial' | 'error' | 'unconfigured'

type ParserServiceStatus = {
  enabled?: boolean
  status?: string
}

type ParserStatusPayload = {
  mineru?: ParserServiceStatus
  pix2text?: ParserServiceStatus
  knowledge_extractor?: { backend?: string }
} | null | undefined

const healthyStatuses = new Set(['ok', 'healthy'])
const unconfiguredStatuses = new Set(['', 'disabled', 'not_configured', 'unknown'])

const normalizedStatus = (service?: ParserServiceStatus) => String(service?.status || '').trim().toLowerCase()
const isHealthy = (service?: ParserServiceStatus) => healthyStatuses.has(normalizedStatus(service))
const isConfigured = (service?: ParserServiceStatus) => isHealthy(service) || service?.enabled === true || !unconfiguredStatuses.has(normalizedStatus(service))

export function parserConnectionState(payload: ParserStatusPayload): ParserConnectionState {
  const services = [payload?.mineru, payload?.pix2text]
  const configured = services.filter(isConfigured)
  const healthy = configured.filter(isHealthy)
  if (!configured.length) return 'unconfigured'
  if (!healthy.length) return 'error'
  return healthy.length === configured.length ? 'connected' : 'partial'
}

export function parserConnectionLabel(state: ParserConnectionState): string {
  return {
    connected: '远程解析已连接',
    partial: '远程解析部分可用',
    error: '远程解析连接异常',
    unconfigured: '远程解析未配置',
  }[state]
}

const serviceStatusLabel = (service?: ParserServiceStatus) => ({
  ok: '正常',
  healthy: '正常',
  disabled: '未配置',
  not_configured: '未配置',
  unreachable: '不可达',
  unknown: '未知',
}[normalizedStatus(service)] || normalizedStatus(service) || '未知')

export function parserConnectionDetail(payload: ParserStatusPayload): string {
  return `MinerU ${serviceStatusLabel(payload?.mineru)} · Pix2Text（可选）${serviceStatusLabel(payload?.pix2text)} · 知识树 ${payload?.knowledge_extractor?.backend || 'unknown'}`
}
