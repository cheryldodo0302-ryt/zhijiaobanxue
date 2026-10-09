export interface AudioMonitorHandle {
  stream: MediaStream
  context: AudioContext
  source: MediaStreamAudioSourceNode
  gain: GainNode
}

export async function startAudioMonitor(): Promise<AudioMonitorHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioContextCtor) {
    stream.getTracks().forEach(track => track.stop())
    throw new Error('当前浏览器不支持实时耳返')
  }
  const context = new AudioContextCtor()
  const source = context.createMediaStreamSource(stream)
  const gain = context.createGain()
  gain.gain.value = 0.8
  source.connect(gain)
  gain.connect(context.destination)
  return { stream, context, source, gain }
}

export async function stopAudioMonitor(handle: AudioMonitorHandle | null): Promise<void> {
  if (!handle) return
  handle.stream.getTracks().forEach(track => track.stop())
  handle.source.disconnect()
  handle.gain.disconnect()
  if (handle.context.state !== 'closed') await handle.context.close()
}
