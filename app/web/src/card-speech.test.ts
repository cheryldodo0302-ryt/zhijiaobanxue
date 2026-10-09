import { describe, expect, it, vi } from 'vitest'
import { createCardSpeech, speechParts } from './card-speech'

function setup() {
  const spoken: SpeechSynthesisUtterance[] = []
  const voices = [
    { lang: 'zh-CN', localService: true, default: true },
    { lang: 'en-GB', localService: true, default: false },
  ] as SpeechSynthesisVoice[]
  const engine = {
    cancel: vi.fn(), pause: vi.fn(), resume: vi.fn(), getVoices: vi.fn(() => voices),
    speak: vi.fn((utterance: SpeechSynthesisUtterance) => spoken.push(utterance)),
  }
  const error = vi.fn()
  const speech = createCardSpeech(engine as unknown as SpeechSynthesis,
    text => ({ text } as SpeechSynthesisUtterance), error)
  const end = (utterance = spoken.at(-1)!) => utterance.onend?.({} as SpeechSynthesisEvent)
  return { speech, engine, spoken, end, error }
}

describe('bilingual card reading', () => {
  it('keeps English words and phrases intact and strips Markdown decorations', () => {
    expect(speechParts('## 数据库\n**database management** 是 [事务](https://example.com) 的基础。')).toEqual([
      { text: '数据库', lang: 'zh-CN' },
      { text: 'database management', lang: 'en-US' },
      { text: '是 事务 的基础。', lang: 'zh-CN' },
    ])
    expect(speechParts("It's a well-known database.")).toEqual([
      { text: "It's a well-known database.", lang: 'en-US' },
    ])
  })

  it('selects matching voices without turning ordinary words into letters', () => {
    const { speech, spoken, end } = setup()
    speech.start('数据库 database management。', 1.25)
    expect(spoken[0]!.lang).toBe('zh-CN')
    end()
    expect(spoken[1]!.text).toBe('database management。')
    expect(spoken[1]!.voice?.lang).toBe('en-GB')
    expect(spoken[1]!.rate).toBe(1.25)
    end()
    expect(speech.state.value).toBe('idle')
  })

  it('pauses and resumes the current utterance without starting again', () => {
    const { speech, engine, spoken } = setup()
    speech.start('Hello world.')
    speech.pause()
    expect(speech.state.value).toBe('paused')
    expect(engine.pause).toHaveBeenCalledOnce()
    speech.resume()
    expect(speech.state.value).toBe('speaking')
    expect(engine.resume).toHaveBeenCalledTimes(2)
    expect(spoken).toHaveLength(1)
  })

  it('does not advance to the next language while paused', () => {
    const { speech, spoken, end } = setup()
    speech.start('中文 English')
    speech.pause()
    end()
    expect(spoken).toHaveLength(1)
    speech.resume()
    expect(spoken[1]!.text).toBe('English')
  })

  it('ignores late callbacks after stopping or replacing a card', () => {
    const { speech, spoken, end, error } = setup()
    speech.start('旧卡片 old card')
    const old = spoken[0]!
    speech.stop()
    end(old)
    expect(spoken).toHaveLength(1)
    speech.start('new card')
    old.onerror?.({ error: 'canceled' } as SpeechSynthesisErrorEvent)
    expect(error).not.toHaveBeenCalled()
    expect(speech.state.value).toBe('speaking')
    expect(spoken.at(-1)!.text).toBe('new card')
  })

  it('uses the language hint when voices are not loaded and reports real errors', () => {
    const { speech, engine, spoken, error } = setup()
    engine.getVoices.mockReturnValue([])
    speech.start('database')
    expect(spoken[0]!.lang).toBe('en-US')
    expect(spoken[0]!.voice).toBeNull()
    spoken[0]!.onerror?.({ error: 'synthesis-failed' } as SpeechSynthesisErrorEvent)
    expect(error).toHaveBeenCalledOnce()
    expect(speech.state.value).toBe('idle')
  })

  it('does not start for empty or formatting-only content', () => {
    const { speech, spoken } = setup()
    expect(speech.start('  ** \n ## ')).toBe(false)
    expect(speech.state.value).toBe('idle')
    expect(spoken).toEqual([])
  })
})
