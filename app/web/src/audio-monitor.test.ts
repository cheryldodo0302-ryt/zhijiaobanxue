import { describe, expect, it, vi } from 'vitest'
import { stopAudioMonitor, type AudioMonitorHandle } from './audio-monitor'

describe('audio monitor cleanup', () => {
  it('stops every track, disconnects nodes and closes the context', async () => {
    const stop = vi.fn()
    const sourceDisconnect = vi.fn()
    const gainDisconnect = vi.fn()
    const close = vi.fn().mockResolvedValue(undefined)
    const handle = {
      stream: { getTracks: () => [{ stop }, { stop }] },
      source: { disconnect: sourceDisconnect },
      gain: { disconnect: gainDisconnect },
      context: { state: 'running', close },
    } as unknown as AudioMonitorHandle

    await stopAudioMonitor(handle)

    expect(stop).toHaveBeenCalledTimes(2)
    expect(sourceDisconnect).toHaveBeenCalledOnce()
    expect(gainDisconnect).toHaveBeenCalledOnce()
    expect(close).toHaveBeenCalledOnce()
  })

  it('does not close an already closed context', async () => {
    const close = vi.fn()
    const handle = {
      stream: { getTracks: () => [] },
      source: { disconnect: vi.fn() },
      gain: { disconnect: vi.fn() },
      context: { state: 'closed', close },
    } as unknown as AudioMonitorHandle

    await stopAudioMonitor(handle)

    expect(close).not.toHaveBeenCalled()
  })
})
