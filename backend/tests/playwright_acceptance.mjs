// 独立浏览器验收；依赖装在项目外，数据库与服务仅用于本次运行。
// npm install --prefix /tmp/opencode/words-playwright playwright
// /tmp/opencode/words-playwright/node_modules/.bin/playwright install chromium
// node backend/tests/playwright_acceptance.mjs
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/tmp/opencode/words-playwright/node_modules/playwright')
const root = path.resolve(import.meta.dirname, '../..')
const output = await mkdtemp('/tmp/opencode/words-browser-')
const env = { ...process.env, WORDS_DB: `${output}/words.sqlite3`, WORDS_HOSTS: '127.0.0.1', WORDS_HTTPS: '0' }
function python(code) {
  const r = spawnSync(`${root}/.venv/bin/python`, ['-c', code], { cwd: root, env, encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  return r.stdout.trim()
}
const port = python('import socket\ns=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
const baseURL = `http://127.0.0.1:${port}`
python("from backend.app.db import initialize,connect\nfrom argon2 import PasswordHasher\ninitialize()\nwith connect() as db: db.execute(\"INSERT INTO users(username,password_hash,role) VALUES(?,?,'parent')\",('browserparent',PasswordHasher().hash('Browser-test-123')))" )
const server = spawn(`${root}/.venv/bin/python`, ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', port], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] })
let logs = ''; server.stdout.on('data', d => logs += d); server.stderr.on('data', d => logs += d)
let browser, page
const checks = [], errors = []
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`${baseURL}/api/health`)).ok) break } catch {}
    await new Promise(r => setTimeout(r, 100))
  }
  browser = await chromium.launch({ headless: true })
  console.log('Browser:', browser.version(), 'URL:', baseURL, 'Artifacts:', output)
  const setup = await browser.newContext({ baseURL })
  const login = await (await setup.request.post('/api/auth/login', { data: { username: 'browserparent', password: 'Browser-test-123' }, headers: { Origin: baseURL } })).json()
  async function post(request, route, data, csrf = login.csrf_token) {
    const response = await request.post(route, { data, headers: { Origin: baseURL, 'X-CSRF-Token': csrf } })
    assert.equal(response.ok(), true, `${route}: ${response.status()} ${await response.text()}`)
    return response.json()
  }
  const child = await post(setup.request, '/api/admin/users', { username: 'browserchild', password: 'Browser-test-123' })
  const ids = {}
  for (const name of ['A', 'B', 'C', 'D']) {
    ids[name] = (await post(setup.request, '/api/decks/import', { user_id: child.id, name: `验收${name}`, items: [1, 2].map(n => ({ word: `${name.toLowerCase()}word${n}`, meaning: `${name}中文${n}` })) })).id
  }
  python(`from backend.app.db import connect\nwith connect() as db:\n db.execute('UPDATE words SET stage=0,due_at=0 WHERE deck_id IN (?,?)',(${ids.C},${ids.D}))\n db.execute('UPDATE words SET stage=-1 WHERE deck_id=? AND word=?',(${ids.C},'cword2'))`)
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 900 } })
  page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await page.getByLabel('用户名', { exact: true }).fill('browserchild')
  await page.getByLabel('密码', { exact: true }).fill('Browser-test-123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('heading', { name: '你好，browserchild。' }).waitFor()
  const card = name => page.locator('article.deck-card').filter({ has: page.getByRole('heading', { name: `验收${name}`, exact: true }) })
  const active = async () => (await (await context.request.get('/api/study/active')).json()).sessions
  const waitText = async text => page.getByText(text, { exact: true }).waitFor()
  async function noOverflow(label, width) {
    await page.setViewportSize({ width, height: 900 })
    const sizes = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
    assert.ok(sizes.document <= sizes.viewport, `${label}: ${JSON.stringify(sizes)}`)
    await page.screenshot({ path: `${output}/${label}-${width}.png`, fullPage: true })
  }
  for (const name of ['A', 'B']) {
    await card(name).getByRole('button', { name: '开始学习 →' }).click()
    await page.locator('#answer').fill('wrong')
    await page.getByRole('button', { name: '提交答案', exact: true }).click()
    await waitText('还没记牢，继续后重输同一个词。')
    await noOverflow(`study-${name}`, 480)
    await page.getByRole('button', { name: '← 返回词库', exact: true }).click()
    await card(name).getByText('第 1 轮 · 已答 1/2', { exact: true }).waitFor()
  }
  let sessions = await active()
  assert.equal(sessions.length, 2)
  assert.deepEqual(sessions.map(s => [s.deck_id, s.answered, s.round_number]), [[ids.A, 1, 1], [ids.B, 1, 1]])
  for (const name of ['A', 'B', 'A']) {
    await card(name).getByRole('button', { name: '继续学习 →' }).click()
    const before = await (await context.request.get(`/api/study/${sessions.find(s => s.deck_id === ids[name]).id}/next`)).json()
    await page.reload()
    await page.getByRole('heading', { name: '你好，browserchild。' }).waitFor()
    await card(name).getByRole('button', { name: '继续学习 →' }).click()
    await page.getByRole('heading', { name: before.item.meaning, exact: true }).waitFor()
    const after = await (await context.request.get(`/api/study/${sessions.find(s => s.deck_id === ids[name]).id}/next`)).json()
    assert.deepEqual(after, before)
    await page.getByRole('button', { name: '← 返回词库', exact: true }).click()
  }
  checks.push('A/B独立批次；均已答1/2；A/B/A切换及刷新后手动继续，当前题及队列响应完全一致')
  await page.getByRole('checkbox', { name: '验收C（1 词可复习）' }).check()
  await page.getByRole('checkbox', { name: '验收D（2 词可复习）' }).check()
  await page.getByRole('button', { name: '复习所选词库（3 词）', exact: true }).click()
  await page.locator('#answer').waitFor()
  sessions = await active()
  assert.equal(sessions.length, 4)
  assert.deepEqual(sessions.filter(s => s.kind === 'review').map(s => [s.deck_id, s.total]), [[ids.C, 1], [ids.D, 2]])
  python("from backend.app.db import connect\nwith connect() as db:\n assert db.execute(\"SELECT count(*) FROM study_items i JOIN words w ON w.id=i.word_id JOIN study_sessions s ON s.id=i.session_id WHERE s.kind='review' AND w.stage<0\").fetchone()[0]==0")
  checks.push('到期复习C/D多选生成两个独立批次，合计3词；due_at=0但stage=-1的cword2被排除')
  await page.getByRole('button', { name: '← 返回词库', exact: true }).click()
  await noOverflow('decks', 1280); await noOverflow('decks', 480)
  python(`import time\nfrom backend.app.db import connect\nwith connect() as db: db.execute('UPDATE attempts SET created_at=? WHERE item_id IN (SELECT i.id FROM study_items i JOIN words w ON w.id=i.word_id WHERE w.deck_id=?)',(time.time()-40*86400,${ids.A}))`)
  await page.getByRole('button', { name: '总体统计', exact: true }).click()
  const panel = page.getByRole('region', { name: '高频错词', exact: true })
  await panel.getByText(/bword/).waitFor()
  assert.equal(await panel.getByText(/aword/).count(), 0)
  assert.equal(await page.locator('.statistics-bar progress').count() > 4, true)
  const values = await page.locator('.statistics-bar progress').evaluateAll(nodes => nodes.map(n => ({ label: n.getAttribute('aria-label'), value: n.value, max: n.max })))
  assert.equal(values.find(v => v.label === '验收C过关比例').value, 0.5)
  assert.equal(values.find(v => v.label === '验收D过关比例').value, 1)
  await noOverflow('statistics', 1280); await noOverflow('statistics', 480)
  await page.getByRole('combobox').nth(1).selectOption('all')
  await panel.getByText(/aword/).waitFor()
  await page.getByRole('combobox').nth(0).selectOption(String(ids.A))
  await page.getByRole('progressbar', { name: '验收A过关比例', exact: true }).waitFor()
  assert.equal(await page.getByRole('progressbar', { name: '验收B过关比例', exact: true }).count(), 0)
  await panel.getByText(/aword/).waitFor()
  assert.equal(await panel.getByText(/bword/).count(), 0)
  await page.getByRole('combobox').nth(1).selectOption('30')
  await waitText('此范围暂无错词')
  checks.push('总体统计真实progress bars及C=50%/D=100%；7天/全部/30天历史筛选；词库A筛选及错词显示/排除')
  checks.push('480/1280词库页与总体统计无整页横向溢出；480学习页无整页溢出')
  assert.deepEqual(errors, [])
  const result = { browser: browser.version(), baseURL, output, checks, errors, bars: values }
  await writeFile(`${output}/result.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
} catch (error) {
  if (page) await page.screenshot({ path: `${output}/failure.png`, fullPage: true }).catch(() => {})
  console.error('FAIL artifacts:', output)
  throw error
} finally {
  await browser?.close()
  server.kill('SIGTERM')
  await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit', resolve))
  await writeFile(`${output}/server.log`, logs)
}
