// Regression acceptance with mocked HTTP: no existing accounts or data are changed.
// node scripts/knowledge_startup_browser.cjs <playwright module path>
const assert = require('node:assert/strict')
const path = require('node:path')
const net = require('node:net')
const { spawn } = require('node:child_process')
const { chromium } = require(process.argv[2])

async function main() {
  const socket = net.createServer()
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve))
  const port = socket.address().port
  await new Promise(resolve => socket.close(resolve))
  const web = path.resolve(__dirname, '../web')
  const server = spawn(process.execPath, [path.join(web, 'node_modules/vite/bin/vite.js'),
    'preview', '--configLoader', 'runner', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: web, windowsHide: true, stdio: 'pipe' })
  const url = `http://127.0.0.1:${port}`
  let browser
  try {
    for (let attempt = 0; attempt < 100; attempt++) {
      try { if ((await fetch(url)).ok) break } catch {}
      if (server.exitCode !== null) throw new Error('Vite preview failed to start')
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    browser = await chromium.launch({ channel: 'chrome', headless: true })
    const courses = [{ course_id: 'one', course_name: '已有数据库课程' },
      { course_id: 'two', course_name: '已有计算机课程' }]
    const failures = []
    for (const scenario of ['parser-pending', 'courses-failed', 'empty', 'jobs-failed']) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
      page.setDefaultTimeout(10000)
      page.on('pageerror', error => failures.push(error.message))
      let courseRequests = 0
      let releaseParser
      const parserGate = new Promise(resolve => { releaseParser = resolve })
      await page.route('**/api/v1/**', async route => {
        const endpoint = new URL(route.request().url()).pathname.replace('/api/v1', '')
        let status = 200, json = []
        if (endpoint === '/auth/refresh') json = { access_token: 'mock-token', user: {
          user_id: 'teacher', username: 'teacher', role: 'teacher', display_name: '演示教师', must_change_password: false,
        } }
        else if (endpoint === '/teacher/courses') {
          courseRequests++
          if (scenario === 'courses-failed' && courseRequests === 1) { status = 503; json = { detail: '课程加载失败，请重试' } }
          else json = scenario === 'empty' ? [] : courses
        } else if (endpoint === '/system/parser-status') {
          if (scenario === 'parser-pending') await parserGate
          status = 503; json = { detail: '解析服务不可达' }
        } else if (endpoint.endsWith('/knowledge-workflow')) json = { can_publish: false, blockers: [], documents: [] }
        else if (endpoint === '/teacher/ai-settings') json = { has_api_key: false }
        else if (scenario === 'jobs-failed' && endpoint.endsWith('/ingestion-jobs')) { status = 503; json = { detail: '资料加载失败' } }
        await route.fulfill({ status, json }).catch(() => {})
      })
      await page.goto(`${url}/knowledge`)
      const selector = page.locator('.resource-course-select .el-select')
      if (scenario === 'empty') {
        await page.getByText('当前教师账号还没有共享课程', { exact: true }).waitFor()
      } else {
        if (scenario === 'courses-failed') {
          await page.getByRole('button', { name: '重新加载课程' }).click()
        }
        await page.getByRole('button', { name: '上传资料', exact: true }).waitFor()
        await page.waitForFunction(() => !document.querySelector('.resource-course-select .el-select')?.classList.contains('is-disabled'))
        assert.equal(await page.getByText('当前教师账号还没有共享课程', { exact: true }).count(), 0)
        await selector.click()
        await page.getByRole('option', { name: courses[1].course_name, exact: true }).click()
        assert.match(await selector.innerText(), /已有计算机课程/)
      }
      releaseParser()
      if (scenario === 'parser-pending') await page.getByText('远程解析连接异常', { exact: true }).waitFor()
      await page.close()
      console.log(`PASS ${scenario}`)
    }
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => failures.push(error.message))
    let importRequests = 0, configRequests = 0
    await page.route('**/api/v1/**', async route => {
      const endpoint = new URL(route.request().url()).pathname.replace('/api/v1', '')
      let json = []
      if (endpoint === '/auth/refresh') json = { access_token: 'mock-token', user: {
        user_id: 'teacher', username: 'teacher', role: 'teacher', display_name: '演示教师', must_change_password: false,
      } }
      else if (endpoint === '/teacher/courses') json = courses
      else if (endpoint === '/teacher/classes') json = [{ class_id: 'class-one', class_name: '演示教学班', course_id: 'one', member_count: 0 }]
      else if (endpoint === '/teacher/institution-profile') json = { school_name: '演示学校', campuses: [], majors: [] }
      else if (endpoint === '/system/student-import-config') { configRequests++; return route.abort() }
      else if (endpoint.endsWith('/members/import') || endpoint.endsWith('/members/import-file')) {
        importRequests++
        if (endpoint.endsWith('/members/import')) assert.equal(route.request().postDataJSON().students[0].student_number, '20260001')
        const created = importRequests < 3
        json = { summary: { created: created ? 1 : 0, reused: 0, already_member: created ? 0 : 1, conflict: 0, invalid: 0 },
          results: [{ row: 1, student_number: '20260001', display_name: '张三', status: created ? 'created' : 'already_member',
            message: created ? '已创建学生账号，初始密码为本人学号，首次登录需修改密码' : '该学生已在本班，密码保持不变' }], members: [] }
      }
      await route.fulfill({ json })
    })
    await page.goto(`${url}/teaching`)
    await page.locator('.member-console .el-select').click()
    await page.getByRole('option', { name: '演示教学班（0人）', exact: true }).click()
    await page.getByRole('button', { name: '打开成员工作台', exact: true }).click()
    await page.getByText('新导入学生的初始密码为本人学号', { exact: true }).waitFor()
    const paste = async () => {
      await page.getByPlaceholder('20260001,张三\n20260002,李四').fill('20260001,张三')
      await page.getByRole('button', { name: '导入名单', exact: true }).click()
    }
    await paste()
    await page.getByText('名单处理完成；新建学生账号的初始密码为各自学号，首次登录需修改密码。', { exact: true }).waitFor()
    await page.getByRole('tab', { name: 'CSV / XLSX', exact: true }).click()
    await page.locator('input[type=file][accept=".csv,.xlsx"]').setInputFiles({ name: 'students.csv', mimeType: 'text/csv', buffer: Buffer.from('学号,姓名\n002612,王五\n') })
    await page.waitForFunction(() => document.querySelectorAll('.el-loading-mask').length === 0)
    await page.getByRole('tab', { name: '粘贴名单', exact: true }).click()
    await paste()
    await page.getByText('名单处理完成；已有账号密码保持不变。', { exact: true }).waitFor()
    assert.equal(importRequests, 3)
    assert.equal(configRequests, 0)
    await page.close()
    console.log('PASS student-import-hints (paste, file, existing account)')
    assert.deepEqual(failures, [])
  } finally {
    if (browser) await browser.close()
    server.kill()
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
