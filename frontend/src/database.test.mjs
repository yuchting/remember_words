import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// 执行真实组件逻辑，不挂载页面，不连接真实数据库。
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`
const utils = moduleUrl(await readFile(new URL('./utils.ts', import.meta.url), 'utf8'))
const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
const script = app.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace('nextTick, onMounted,', 'nextTick,')
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  .replace("from './utils'", `from '${utils}'`)
const state = await import(moduleUrl(`const onMounted = () => {};\n${script}\nexport { api, user, session, csrf, busy, error, notice, password, childPassword, resetPasswords, exportPassword, restorePassword, restoreFile, restoreConfirmation, restoreUnderstood, canRestore, selectRestoreFile, exportDatabase, restoreDatabase, personalPassword, personalFile, personalUnderstood, personalConfirmation, canRestorePersonal, restorePersonalBackup, personalExportPassword, exportPersonalBackup, selectPersonalFile };`))
globalThis.window = {}
const file = new File(['SQLite test'], 'backup.sqlite3')
state.user.value = { id: 1, username: 'parent', role: 'parent' }
const prepare = () => {
  state.restoreFile.value = file
  state.restoreUnderstood.value = true
  state.restoreConfirmation.value = '确认覆盖整个数据库'
  state.restorePassword.value = ' 密码 with spaces '
}
prepare()
assert.equal(state.canRestore.value, true)
state.restoreConfirmation.value += ' '
assert.equal(state.canRestore.value, false)
assert.equal(state.restorePassword.value, '')
prepare()
state.selectRestoreFile({ target: { files: [new File(['bad'], 'invalid.json')], value: 'invalid.json' } })
assert.equal(state.restoreFile.value, null)
assert.equal(state.restoreUnderstood.value, false)
assert.equal(state.restorePassword.value, '')
state.selectRestoreFile({ target: { files: [{ name: 'large.db', size: 50 * 1024 * 1024 + 1 }], value: 'large.db' } })
assert.equal(state.restoreFile.value, null)
prepare()
let calls = 0
fetch = async () => { calls++; return Response.json({ detail: '恢复被拒绝' }, { status: 400 }) }
globalThis.confirm = () => false
await state.restoreDatabase()
assert.equal(calls, 0)
confirm = () => true
state.busy.value = true
await state.restoreDatabase()
assert.equal(calls, 0)
state.busy.value = false
await state.restoreDatabase()
assert.equal(calls, 1, '恢复失败不得自动重试')
assert.equal(state.notice.value, '')
assert.match(state.error.value, /恢复被拒绝/)
assert.ok(state.user.value)
assert.equal(state.restorePassword.value, '')
prepare()
state.user.value = { id: 2, username: 'child', role: 'child' }
prepare()
await state.restoreDatabase()
assert.equal(calls, 1, '非家长不能发起恢复')
state.user.value = { id: 1, username: 'parent', role: 'parent' }
prepare()
state.session.value = 123
state.password.value = 'login secret'
state.childPassword.value = 'child secret'
state.resetPasswords.value = { 2: 'reset secret' }
state.exportPassword.value = 'export secret'
fetch = async (url, options) => {
  if (url === '/api/auth/me') return Response.json({ csrf_token: 'csrf-test' })
  calls++
  assert.equal(url, '/api/admin/database/restore')
  assert.equal(options.method, 'POST')
  assert.equal(options.body, file)
  assert.equal(options.headers['Content-Type'], 'application/octet-stream')
  assert.equal(options.headers['X-CSRF-Token'], 'csrf-test')
  assert.equal(options.headers['X-Admin-Password'], encodeURIComponent(' 密码 with spaces '))
  assert.equal(options.headers['X-Restore-Confirmation'], 'OVERWRITE_ALL_DATA')
  return Response.json({ ok: true, backup: 'before-restore.sqlite3' })
}
await state.api('/auth/me')
await state.restoreDatabase()
assert.equal(calls, 2)
assert.equal(state.user.value, null)
assert.equal(state.session.value, null)
assert.equal(state.csrf, '')
assert.equal(state.restoreFile.value, null)
assert.equal(state.restorePassword.value, '')
assert.equal(state.exportPassword.value, '')
assert.equal(state.password.value, '')
assert.equal(state.childPassword.value, '')
assert.deepEqual(state.resetPasswords.value, {})
assert.match(state.notice.value, /before-restore.sqlite3/)
assert.equal(state.error.value, '')

state.user.value = { id: 1, username: 'parent', role: 'parent' }
state.exportPassword.value = ' export 密码 '
let downloaded = false
const link = { click() { downloaded = true }, remove() {} }
globalThis.document = { createElement: () => link, body: { append() {} } }
fetch = async (url, options) => {
  assert.equal(url, '/api/admin/database/export')
  assert.equal(options.method, 'POST')
  assert.deepEqual(JSON.parse(options.body), { password: ' export 密码 ' })
  return new Response(new Blob(['SQLite backup']))
}
await state.exportDatabase()
assert.equal(downloaded, true)
assert.equal(state.exportPassword.value, '')
assert.match(state.notice.value, /密码哈希/)
const personal = new File([JSON.stringify({ format: 'remember_words_user', version: 1, decks: [] })], 'personal.json')
state.user.value = { id: 2, username: 'child', role: 'child' }
const preparePersonal = (file = personal) => {
  state.personalFile.value = file
  state.personalUnderstood.value = true
  state.personalConfirmation.value = '确认覆盖我的学习数据'
  state.personalPassword.value = ' child 密码 '
}
preparePersonal()
assert.equal(state.canRestorePersonal.value, true, '孩子也能恢复个人备份')
state.personalConfirmation.value += ' '
assert.equal(state.canRestorePersonal.value, false)
assert.equal(state.personalPassword.value, '')
state.selectPersonalFile({ target: { files: [file], value: 'backup.sqlite3' } })
assert.equal(state.personalFile.value, null, '个人恢复拒绝整库文件')
preparePersonal(new File(['{}'], 'bad.json'))
let personalCalls = 0
fetch = async () => { personalCalls++; return Response.json({}) }
await state.restorePersonalBackup()
assert.equal(personalCalls, 0)
assert.match(state.error.value, /remember_words_user/)
preparePersonal()
confirm = () => false
await state.restorePersonalBackup()
assert.equal(personalCalls, 0)
confirm = () => true
fetch = async () => { personalCalls++; return Response.json({ detail: '拒绝恢复' }, { status: 400 }) }
await state.restorePersonalBackup()
assert.equal(personalCalls, 1)
assert.equal(state.notice.value, '')
assert.ok(state.user.value)
preparePersonal()
state.session.value = 99
fetch = async (url, options) => {
  if (url === '/api/account/backup/restore') {
    personalCalls++
    assert.equal(options.method, 'POST')
    assert.deepEqual(JSON.parse(options.body), { password: ' child 密码 ', confirmation: 'OVERWRITE_MY_DATA', backup: { format: 'remember_words_user', version: 1, decks: [] } })
    return Response.json({ backup: 'personal-before.json' })
  }
  if (url === '/api/decks') return Response.json([])
  if (url === '/api/reviews/due') return Response.json({ count: 0 })
  throw new Error(`意外请求 ${url}`)
}
await state.restorePersonalBackup()
assert.equal(personalCalls, 2)
assert.equal(state.user.value.username, 'child', '个人恢复保持登录和账户')
assert.equal(state.session.value, null)
assert.equal(state.personalFile.value, null)
assert.equal(state.personalPassword.value, '')
assert.match(state.notice.value, /personal-before.json/)
assert.equal(state.error.value, '')

let finishRead
preparePersonal({ text: () => new Promise(resolve => { finishRead = resolve }) })
const pending = state.restorePersonalBackup()
state.personalConfirmation.value = ''
finishRead(JSON.stringify({ format: 'remember_words_user', version: 1 }))
await pending
assert.equal(personalCalls, 2, '读取期间确认变化不得上传旧文件')
assert.match(state.error.value, /已变化/)
state.personalExportPassword.value = ' child export '
fetch = async (url, options) => {
  assert.equal(url, '/api/account/backup/export')
  assert.deepEqual(JSON.parse(options.body), { password: ' child export ' })
  return Response.json({ format: 'remember_words_user', version: 1 })
}
await state.exportPersonalBackup()
assert.equal(state.personalExportPassword.value, '')
console.log('整库及个人备份：权限、确认、文件校验、失败不重试、请求头、旧状态隔离和敏感状态清理检查通过')
