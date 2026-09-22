import MarkdownIt from 'markdown-it'
import {katex as katexPlugin} from '@mdit/plugin-katex'
import {renderToString} from 'katex'

// Some document parsers return bare TeX instead of $...$ or \[...\].
// Recognize only formula lines; leave prose, code and the stored source intact.
const mathCommand = /\\(?:pi|sigma|rho|alpha|beta|gamma|delta|theta|lambda|mu|omega|Delta|Sigma|Omega|frac|dfrac|tfrac|sqrt|sum|prod|int|lim|log|ln|sin|cos|tan|mathrm|mathbf|mathbb|mathcal|operatorname|begin|left|overline|vec|hat|xrightarrow|rightarrow|Rightarrow|infty|forall|exists|partial|nabla)(?![a-zA-Z])/
const formulaStart = /^\s*(?:\\[a-zA-Z]+(?![a-zA-Z])|[a-zA-Z0-9][a-zA-Z0-9_{}^()+ .-]*[=<>])/u
const options = {trust: false, throwOnError: true, strict: false, maxExpand: 1000, maxSize: 20} as const

function bareFormulaHtml(source: string): string | null {
  if (!formulaStart.test(source) || !mathCommand.test(source)) return null
  if (/[`$]/.test(source) || /\\[()[\]]/.test(source)) return null
  // Ignore command names and braced arguments when checking for surrounding prose.
  let depth = 0
  let outside = ''
  for (const character of source.replace(/\\[a-zA-Z]+|\\[{}]/g, '')) {
    if (character === '{') depth++
    else if (character === '}') depth--
    else if (!depth) outside += character
    if (depth < 0) return null
  }
  if (depth || /[\u3400-\u9fff]|[a-zA-Z]{3,}|[。；：]/u.test(outside)) return null
  try {
    return renderToString(source, {...options, displayMode: true})
  } catch {
    // Incomplete/OCR-damaged expressions remain visible for teacher correction.
    return null
  }
}

export function createKnowledgeMarkdown() {
  const markdown = new MarkdownIt({html: false, linkify: true, breaks: true})
    .use(katexPlugin, {delimiters: 'all', allowInlineWithSpace: true, ...options, throwOnError: false})
  markdown.inline.ruler.before('text', 'bare_formula', (state, silent) => {
    if (silent || state.level > 0) return false
    const lineStart = state.src.lastIndexOf('\n', state.pos - 1) + 1
    if (state.pos > 0 && !/^[ \t]*$/.test(state.src.slice(lineStart, state.pos))) return false
    let end = state.src.indexOf('\n', state.pos)
    if (end < 0 || end > state.posMax) end = state.posMax
    const firstLine = state.src.slice(state.pos, end)
    if (!formulaStart.test(firstLine) || !mathCommand.test(firstLine)) return false
    // Permit a parser's hard line break within a braced expression, but never
    // consume a blank line, Markdown fence or the next explanatory paragraph.
    for (let count = 0; count < 12; count++) {
      const source = state.src.slice(state.pos, end)
      const html = bareFormulaHtml(source)
      if (html) {
        if (!silent) {
          const token = state.push('bare_formula', '', 0)
          token.content = html
        }
        state.pos = end
        return true
      }
      const balance = source.replace(/\\[{}]/g, '').split('').reduce(
        (depth, character) => depth + (character === '{' ? 1 : character === '}' ? -1 : 0), 0,
      )
      if (balance <= 0 || end >= state.posMax) break
      const nextEnd = state.src.indexOf('\n', end + 1)
      const nextLine = state.src.slice(end + 1, nextEnd < 0 ? state.posMax : nextEnd)
      if (!nextLine.trim() || /[`$]|^[ \t]*[#>]/.test(nextLine)) break
      end = nextEnd < 0 ? state.posMax : nextEnd
    }
    return false
  })
  markdown.renderer.rules.bare_formula = (tokens, index) => tokens[index]!.content
  // Recognize unmarked expressions embedded in Chinese prose before Markdown
  // consumes the prose or interprets underscores as emphasis. Explicit math,
  // code spans and links are left to their existing Markdown rules.
  markdown.inline.ruler.after('bare_formula', 'bare_inline_formula', (state, silent) => {
    if (silent || state.level > 0) return false
    const remaining = state.src.slice(state.pos, state.posMax)
    const barrier = remaining.search(/[`$\[\]*!<>\n]|\\[()[\]]/)
    const text = barrier < 0 ? remaining : remaining.slice(0, barrier)
    const segments = text.matchAll(/[A-Za-z0-9\\{}_^=+\-/(),.|~ \t]+/g)
    for (const match of segments) {
      const source = match[0].trim()
      // A command alone in explanatory prose is not enough evidence. Require
      // a scripted variable or an actual math operator/argument.
      if (!/[A-Za-z]\s*[_^]\s*(?:\{|[A-Za-z0-9])|\\(?:frac|sqrt|mathrm|mathbb)\s*\{|\\(?:times|cdots|in|leq|geq|neq)(?![A-Za-z])/.test(source)) continue
      let depth = 0
      let outside = ''
      for (const character of source.replace(/\\[A-Za-z]+|\\[{}]/g, '')) {
        if (character === '{') depth++
        else if (character === '}') depth--
        else if (!depth) outside += character
        if (depth < 0) break
      }
      if (depth || /[A-Za-z]{3,}/.test(outside)) continue
      try {
        const html = renderToString(source, {...options, displayMode: false})
        const start = match.index! + match[0].indexOf(source)
        if (start) state.push('text', '', 0).content = text.slice(0, start)
        state.push('bare_formula', '', 0).content = html
        state.pos += start + source.length
        return true
      } catch {
        // Keep damaged OCR visible instead of silently changing the expression.
      }
    }
    return false
  })
  return markdown
}
