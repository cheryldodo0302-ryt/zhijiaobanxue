import {describe, expect, it} from 'vitest'
import {createKnowledgeMarkdown} from './knowledge-markdown'

const markdown = createKnowledgeMarkdown()
const screenshotFormula = String.raw`\pi _ { \mathrm { S n u m . S m a r n e } , \mathrm { S e r s . S p h o n e } } ( S ) \xrightarrow [ ] { \mathbb { H } } \pi _ { 1 , 2 , 3 , 5 } ( S )`

describe('knowledge preview formulas', () => {
  it('renders unmarked scripted variables and Cartesian products inside Chinese prose', () => {
    const source = String.raw`则D _ { 1 } \times D _ { 2 } \times \cdots \times D _ { n }为笛卡尔积。其中每个元素(d _ { 1 },\ d _ { 2 },\ \cdots,\ d _ { n })称为一个元组(n-Tuple)，元素中每个d _ { i }称为分量(Component)，d _ { i }\in D _ { i }。

若D _ { i }的基数为m _ { i }，则笛卡尔积的基数为`;
    const tokens = markdown.parse(source, {})
    const math = tokens.flatMap(token => token.children || []).filter(token => token.type === 'bare_formula')
    expect(math).toHaveLength(6)
    expect(math.every(token => token.content.includes('class="katex"') && !token.content.includes('katex-error'))).toBe(true)
    const html = markdown.render(source)
    expect(html).toContain('称为一个元组(n-Tuple)')
    expect(html).toContain('称为分量(Component)')
  })

  it('renders the unmarked projection formula from the screenshot without changing its source', () => {
    const source = `【例2.2】找出所有学生的学号、姓名、性别和电话。\n\n解\n\n${screenshotFormula}\n\n执行结果见表2.15。`
    const html = markdown.render(source)
    expect(html).toContain('class="katex-display"')
    expect(html).toContain('<math ')
    expect(html).toContain('执行结果见表2.15。')
    expect(html).not.toContain('katex-error')
    expect(source).toContain(screenshotFormula)
  })

  it('handles a hard line break inside a formula and a formula immediately after prose', () => {
    const html = markdown.render(String.raw`解
\pi_{\mathrm{Snum},
\mathrm{Sname}}(S)
执行结果如下。`)
    expect(html.match(/class="katex-display"/g)).toHaveLength(1)
    expect(html).toContain('执行结果如下。')
  })

  it('still supports inline and display math delimiters', () => {
    for (const source of [String.raw`面积 $ \pi r^2 $`, String.raw`\(\frac{1}{2}\)`, String.raw`\[\sum_{i=1}^n i\]`, '$$\nx^2\n$$']) {
      expect(markdown.render(source)).toContain('class="katex')
    }
  })

  it('renders an equation beginning with a variable and keeps links intact', () => {
    expect(markdown.render(String.raw`x = \frac{1}{2}`)).toContain('class="katex-display"')
    expect(markdown.render(String.raw`[\pi_{1,2}(S)](https://example.com)`)).not.toContain('class="katex-display"')
  })

  it('does not turn code examples, paths or explanatory text into formulas', () => {
    for (const source of ['```latex\n' + screenshotFormula + '\n```', '`' + screenshotFormula + '`', '    ' + screenshotFormula, String.raw`C:\notes\pi.txt`, String.raw`使用 \pi 表示圆周率`, String.raw`\pi represents pi`]) {
      expect(markdown.render(source)).not.toContain('class="katex')
    }
  })

  it('preserves incomplete expressions and escapes uploaded HTML', () => {
    const html = markdown.render(String.raw`\frac{1}{

下一段说明

<img src=x onerror=alert(1)>`)
    expect(html).not.toContain('class="katex')
    expect(html).toContain('下一段说明')
    expect(html).toContain('&lt;img')
    expect(html).not.toContain('<img')
  })
})
