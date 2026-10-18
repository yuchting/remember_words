import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`
const utils = moduleUrl(await readFile(new URL('./utils.ts', import.meta.url), 'utf8'))
const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
for (const url of ['https://portal.azure.com/#view/Microsoft_Azure_Billing/ModernBillingMenuBlade/~/Overview', 'https://console.cloud.google.com/billing']) {
  assert.ok(app.includes(`href="${url}" target="_blank" rel="noopener noreferrer">查看额度／账单（新窗口）</a>`), '官方账单入口使用固定链接，安全打开新窗口')
}
for (const provider of ['azure', 'google']) {
  assert.ok(app.includes(`:placeholder="speechSettings.${provider}.key_set ? '*****' : '请输入 API Key'"`), '已保存密钥仅以占位符显示，不写入表单值')
  assert.ok(app.includes(`aria-describedby="${provider}-key-help"`), '密钥保留规则与输入框关联')
}
assert.equal((app.match(/class="speech-key-status" role="status"/g) ?? []).length, 2, '状态紧邻两家密钥输入框')
const script = app.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace('nextTick, onMounted,', 'nextTick,').replace(', onUnmounted }', ' }')
  .replace('import.meta.env.BASE_URL', "'/words/'")
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`).replace("from './utils'", `from '${utils}'`)
const spoken = [], players = [], revoked = [], requests = []
globalThis.window = { speechSynthesis: {} }
globalThis.speechSynthesis = { cancel() {}, speak: value => spoken.push(value.text) }
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
globalThis.Audio = class {
  constructor(src) { this.src = src; players.push(this) }
  play() { this.onplaying?.(); return Promise.resolve() }
  pause() {} removeAttribute() {} load() {}
}
URL.createObjectURL = () => `blob:${players.length}`
URL.revokeObjectURL = value => revoked.push(value)
const state = await import(moduleUrl(`const onMounted = () => {}; const onUnmounted = () => {};\n${script}\nexport { speak, clearAudio, error, speechSettings, speechKeys, speechClear, saveSpeech, openSpeech, testSpeech, testSpeechProvider, notice, clearSpeechCache, speechCache, page };`))
let enabled = true, fallback = false, fail = false, pending
const settings = { primary: 'azure', fallback: 'google', browser_fallback: false, azure: { region: 'eastus', voice: 'en-US-JennyNeural', key_set: true }, google: { voice: 'en-US-Neural2-F', key_set: false } }
fetch = async (url, options) => {
  requests.push({ url, ...options })
  assert.ok(url.startsWith('/words/api/'))
  if (options.method !== 'GET') assert.ok('X-CSRF-Token' in options.headers)
  if (url.endsWith('/speech/settings')) return Response.json({ enabled, browser_fallback: fallback })
  if (url.endsWith('/speech/cache')) return Response.json({ files: options.method === 'DELETE' ? 0 : 2, bytes: 100, max_bytes: 104857600 })
  if (url.endsWith('/admin/speech')) return Response.json(settings)
  if (pending) return pending
  if (fail) return Response.json({ detail: '云端失败' }, { status: 502 })
  return new Response(new Blob(['audio'], { type: 'audio/mpeg' }))
}
await state.speak('word', undefined, 7)
assert.deepEqual(JSON.parse(requests.at(-1).body), { word_id: 7, kind: 'word' })
assert.equal(players.length, 1)
players.at(-1).onended()
assert.equal(revoked.length, 1)
await state.speak('Example.', undefined, 7, 'example')
assert.equal(JSON.parse(requests.at(-1).body).kind, 'example')
state.clearAudio()
fail = true
await state.speak('word', undefined, 7)
assert.equal(spoken.length, 0)
assert.equal(state.error.value, '云端失败')
fallback = true
await state.speak('word', undefined, 7)
assert.deepEqual(spoken, ['word'])
enabled = false
await state.speak('old', undefined, 7)
assert.deepEqual(spoken, ['word', 'old'])
enabled = true; fail = false
let resolve
pending = new Promise(done => { resolve = done })
const stale = state.speak('stale', undefined, 7)
await new Promise(done => setImmediate(done))
state.clearAudio()
const before = players.length
resolve(new Response(new Blob(['stale'])))
await stale
assert.equal(players.length, before, '离开页面后旧请求不得播放或创建 URL')
pending = null
await state.openSpeech()
assert.equal(state.page.value, 'speech')
state.speechKeys.value.azure = 'secret'
await state.saveSpeech()
const put = requests.find(request => request.method === 'PUT')
assert.equal(JSON.parse(put.body).azure.key, 'secret')
assert.equal(state.speechKeys.value.azure, '', '保存后清除输入密钥')
await state.testSpeech()
assert.ok(requests.at(-1).url.endsWith('/admin/speech/test'))
state.speechKeys.value.google = 'draft-google-key'
await state.testSpeechProvider('google')
assert.ok(requests.at(-1).url.endsWith('/admin/speech/test-provider'))
assert.deepEqual(JSON.parse(requests.at(-1).body), { provider: 'google', config: { voice: settings.google.voice, key: 'draft-google-key', clear_key: false } })
assert.equal(state.speechKeys.value.google, 'draft-google-key', '测试不保存或清空草稿')
assert.match(state.notice.value, /Google 测试音频已开始播放/)
fail = true
await state.testSpeechProvider('azure')
assert.match(state.error.value, /云端失败/)
assert.equal(state.notice.value, '')
fail = false
state.clearAudio()
globalThis.confirm = () => false
const count = requests.length
await state.clearSpeechCache()
assert.equal(requests.length, count)
globalThis.confirm = () => true
await state.clearSpeechCache()
assert.equal(requests.at(-1).method, 'DELETE')
assert.equal(state.speechCache.value.files, 0)
console.log('云端朗读、回退、旧请求隔离、密钥表单和缓存确认检查通过（替身测试）')
