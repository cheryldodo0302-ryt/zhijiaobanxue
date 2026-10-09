import { describe, expect, it } from 'vitest'
import { parserConnectionDetail, parserConnectionLabel, parserConnectionState } from './parser-status'

describe('parser connection presentation', () => {
  it('treats a healthy primary parser with optional Pix2Text absent as connected', () => {
    const payload = {
      mineru: { enabled: true, status: 'ok' },
      pix2text: { enabled: false, status: 'not_configured' },
      knowledge_extractor: { backend: 'builtin' },
    }
    expect(parserConnectionState(payload)).toBe('connected')
    expect(parserConnectionLabel(parserConnectionState(payload))).toBe('远程解析已连接')
    expect(parserConnectionDetail(payload)).toContain('Pix2Text（可选）未配置')
  })

  it('distinguishes partial availability, total failure, and no configuration', () => {
    expect(parserConnectionState({
      mineru: { enabled: true, status: 'healthy' },
      pix2text: { enabled: true, status: 'unreachable' },
    })).toBe('partial')
    expect(parserConnectionState({
      mineru: { enabled: true, status: 'unreachable' },
      pix2text: { enabled: true, status: 'unreachable' },
    })).toBe('error')
    expect(parserConnectionState({
      mineru: { enabled: false, status: 'not_configured' },
      pix2text: { enabled: false, status: 'not_configured' },
    })).toBe('unconfigured')
  })
})
