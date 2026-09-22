import { ref } from 'vue'

export type SpeechPart = { text: string; lang: 'zh-CN' | 'en-US' }

export function speechParts(content: string): SpeechPart[] {
  const text = content
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/[*_`~]/g, '')
  const parts: SpeechPart[] = []
  const english = /[A-Za-z][A-Za-z0-9]*(?:['’\-][A-Za-z0-9]+)*(?:[ \t]+[A-Za-z][A-Za-z0-9]*(?:['’\-][A-Za-z0-9]+)*)*/g
  for (const sentence of text.split(/(?<=[。！？.!?；;])\s*|\n+/u)) {
    let offset = 0
    const add = (value: string, lang: SpeechPart['lang']) => {
      if (!value.trim()) return
      if (!/[\p{L}\p{N}]/u.test(value) && parts.length) parts[parts.length - 1]!.text += value
      else if (/[\p{L}\p{N}]/u.test(value)) parts.push({ text: value.trim(), lang })
    }
    for (const match of sentence.matchAll(english)) {
      add(sentence.slice(offset, match.index), 'zh-CN')
      add(match[0], 'en-US')
      offset = match.index! + match[0].length
    }
    add(sentence.slice(offset), 'zh-CN')
  }
  return parts
}

export function createCardSpeech(
  synth: SpeechSynthesis,
  makeUtterance = (text: string) => new SpeechSynthesisUtterance(text),
  onError = () => {},
) {
  const state = ref<'idle' | 'speaking' | 'paused'>('idle')
  let generation = 0
  let parts: SpeechPart[] = []
  let index = 0
  let rate = 1
  let current: SpeechSynthesisUtterance | null = null

  function stop() {
    generation++
    current = null
    parts = []
    state.value = 'idle'
    synth.cancel()
  }

  function next(token: number) {
    if (token !== generation || state.value === 'paused') return
    const part = parts[index]
    if (!part) { current = null; state.value = 'idle'; return }
    const utterance = makeUtterance(part.text)
    current = utterance
    utterance.lang = part.lang
    utterance.rate = rate
    const voices = synth.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(part.lang.slice(0, 2)))
    utterance.voice = voices.find(voice => voice.localService && voice.default)
      || voices.find(voice => voice.localService) || voices.find(voice => voice.default) || voices[0] || null
    utterance.onend = () => {
      if (token !== generation) return
      current = null
      index++
      next(token)
    }
    utterance.onerror = () => {
      if (token !== generation) return
      stop()
      onError()
    }
    synth.speak(utterance)
  }

  function start(text: string, speed = 1) {
    stop()
    parts = speechParts(text)
    index = 0
    rate = speed
    if (!parts.length) return false
    state.value = 'speaking'
    // Some browsers retain their paused flag even after cancel().
    synth.resume()
    next(generation)
    return true
  }

  function pause() {
    if (state.value !== 'speaking') return
    state.value = 'paused'
    synth.pause()
  }

  function resume() {
    if (state.value !== 'paused') return
    state.value = 'speaking'
    synth.resume()
    if (!current) next(generation)
  }

  return { state, start, stop, pause, resume }
}
