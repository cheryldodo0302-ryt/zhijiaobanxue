// Invoked by e2e_student_portraits.py with isolated demo accounts and local servers.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const { chromium } = require(config.playwright)

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const errors = []
  let lastPage
  try {
    async function pageFor(username, password, target) {
      const context = await browser.newContext({ viewport: { width: 1500, height: 1100 } })
      const page = await context.newPage()
      page.setDefaultTimeout(15000)
      lastPage = page
      page.on('pageerror', error => errors.push(error.message))
      await page.goto(`${config.url}/login?returnTo=${encodeURIComponent(target)}`)
      await page.locator('input[autocomplete=username]').fill(username)
      await page.locator('input[autocomplete=current-password]').fill(password)
      await page.getByRole('button', { name: '登录', exact: true }).click()
      await page.waitForURL(`${config.url}${target}`)
      return page
    }
    const teacher = await pageFor('demo_teacher', config.teacherPassword, '/student-portraits')
    await teacher.getByText('演示学生', { exact: true }).first().waitFor()
    await teacher.getByRole('tab', { name: '发布作业 / 考试' }).click()
    const panel = teacher.getByRole('tabpanel').filter({ has: teacher.getByRole('heading', { name: '发布班级任务' }) })
    await panel.getByLabel('任务名称').fill('浏览器验收作业')
    await panel.getByPlaceholder('选择日期与时间').fill(config.due)
    await panel.getByPlaceholder('选择日期与时间').press('Enter')
    await panel.getByLabel('已发布题库').click()
    await teacher.getByRole('option', { name: '题库 · 版本 1', exact: true }).click()
    await panel.getByText('SQL 查询的核心关键字是？', { exact: true }).waitFor()
    await panel.locator('thead .el-checkbox').first().click()
    const publication = teacher.waitForResponse(r => r.url().endsWith('/tasks') && r.request().method() === 'POST')
    await panel.getByRole('button', { name: '发布给当前班级（3 题）' }).click()
    const published = await publication
    assert.equal(published.status(), 200, await published.text())

    const student = await pageFor('demo_student', config.studentPassword, '/student/tasks')
    const row = student.getByRole('row').filter({ hasText: '浏览器验收作业' })
    await row.getByRole('button', { name: '查看与作答' }).click()
    await student.getByText('A. SELECT', { exact: true }).click()
    await student.getByText('N. 错误', { exact: true }).click()
    await student.getByText('A. 选择', { exact: true }).click()
    await student.getByText('C. 投影', { exact: true }).click()
    const submission = student.waitForResponse(r => r.url().endsWith('/submissions') && r.request().method() === 'POST')
    await student.getByRole('button', { name: '正式提交作业' }).click()
    const graded = await submission
    assert.equal(graded.status(), 200, await graded.text())
    assert.equal((await graded.json()).score, 100)

    await teacher.getByRole('tab', { name: '学生画像', exact: true }).click()
    await teacher.getByRole('button', { name: '刷新', exact: true }).click()
    await teacher.getByRole('row').filter({ hasText: '浏览器验收作业' }).getByText('第 1/1，前 100%（动态）', { exact: true }).waitFor()
    await teacher.screenshot({ path: path.join(config.output, 'teacher-portrait.png'), fullPage: true })
    const evaluation = teacher.waitForResponse(r => r.url().endsWith('/evaluate'))
    await teacher.getByRole('button', { name: '生成评价', exact: true }).click()
    assert.equal((await (await evaluation).json()).status, 'failed')

    await student.getByRole('button', { name: '自习室', exact: true }).click()
    await student.getByText('本次关联课程，并授权任课教师查看汇总', { exact: true }).click()
    await student.getByText('课程与教学班', { exact: true }).click()
    await student.getByRole('option').last().click()
    const started = student.waitForResponse(r => r.url().endsWith('/study-room/start'))
    await student.getByRole('button', { name: '开始自习', exact: true }).click()
    assert.equal((await started).status(), 200)
    await student.getByRole('button', { name: '结束并保存', exact: true }).click()
    const finished = student.waitForResponse(r => r.url().endsWith('/study-room/finish'))
    await student.getByRole('button', { name: '确定', exact: true }).click()
    assert.equal((await finished).status(), 200)
    const updated = teacher.waitForResponse(r => /\/portraits\/demo_student_001\?/.test(r.url()) && r.request().method() === 'GET')
    await teacher.getByRole('button', { name: '刷新', exact: true }).click()
    const shared = (await (await updated).json()).metrics.study
    assert.equal(shared.sessions, 1)
    assert.equal(shared.focus_reference, null)
    await student.getByRole('button', { name: '撤销授权', exact: true }).click()
    await student.getByText('已撤销授权，相关教师评价已失效', { exact: true }).waitFor()
    const revoked = teacher.waitForResponse(r => /\/portraits\/demo_student_001\?/.test(r.url()) && r.request().method() === 'GET')
    await teacher.getByRole('button', { name: '刷新', exact: true }).click()
    const hidden = (await (await revoked).json()).metrics.study
    assert.equal(hidden.authorized, false)
    assert.equal(hidden.sessions, 0)
    await teacher.setViewportSize({ width: 900, height: 1000 })
    await teacher.screenshot({ path: path.join(config.output, 'teacher-portrait-narrow.png'), fullPage: true })
    assert.deepEqual(errors, [])
    console.log('BROWSER PASS: publish, answer, portrait, AI failure, consent, finish, revoke; no page errors')
  } catch (error) {
    if (lastPage) {
      await lastPage.screenshot({path:path.join(config.output,'browser-failure.png'),fullPage:true})
      fs.writeFileSync(path.join(config.output,'browser-failure.html'),await lastPage.content())
    }
    throw error
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
