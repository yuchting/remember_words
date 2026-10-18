import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// 执行真实组件逻辑；时钟、语音、API 均使用替身，不访问正式库。
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`
const utils = moduleUrl(await readFile(new URL('./utils.ts', import.meta.url), 'utf8'))
const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
const script = app.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace('nextTick, onMounted,', 'nextTick,')
  .replace(', onUnmounted }', ' }')
  .replace('import.meta.env.BASE_URL', "'/'")
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  .replace("from './utils'", `from '${utils}'`)
let now = 0
let tick
Object.defineProperty(globalThis, 'performance', { value: { now: () => now }, configurable: true })
globalThis.setInterval = fn => { tick = fn; return 1 }
globalThis.clearInterval = () => { tick = undefined }
globalThis.window = { speechSynthesis: {} }
const spoken = []
globalThis.speechSynthesis = { cancel() {}, speak: value => spoken.push(value.text) }
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
const state = await import(moduleUrl(`const onMounted = () => {}; const onUnmounted = () => {};\n${script}\nexport { session, item, answer, answered, content, busy, cooldownSeconds, submit, reveal, continueStudy, studyEnter, editingExample, resetFluency, key, beforeInput, nativeInput, report, reportWords };`))
globalThis.document = { body: {}, activeElement: null }
state.session.value = 1
state.item.value = { id: 2 }
state.answer.value = 'word'
let nextCalls = 0
fetch = async url => {
  if (url.endsWith('/answer')) return Response.json({ word: 'word', meaning: '单词', correct: true })
  if (url.endsWith('/reveal')) return Response.json({ word: 'word', meaning: '单词' })
  assert.ok(url.endsWith('/next'))
  nextCalls++
  return Response.json({ complete: false, item: { id: 3 } })
}
await state.reveal()
assert.deepEqual(spoken, ['word'])
assert.equal(state.cooldownSeconds.value, 0)
await state.submit()
assert.deepEqual(spoken, ['word', 'word'])
assert.equal(state.cooldownSeconds.value, 2)
await state.continueStudy()
assert.equal(nextCalls, 0)
now = 1001; tick()
assert.equal(state.cooldownSeconds.value, 1)
await state.continueStudy()
assert.equal(nextCalls, 0)
now = 2000; tick()
assert.equal(state.cooldownSeconds.value, 0)
assert.equal(nextCalls, 0, '倒计时结束不自动继续')
const enter = extra => state.studyEnter({ key: 'Enter', target: document.body, preventDefault() {}, ...extra })
enter({ repeat: true })
enter({ isComposing: true })
state.editingExample.value = true
enter({})
state.editingExample.value = false
state.busy.value = true
enter({})
state.busy.value = false
assert.equal(nextCalls, 0)
enter({})
await new Promise(resolve => setImmediate(resolve))
assert.equal(nextCalls, 1)
assert.equal(state.answered.value, false)
state.answer.value = ''
await state.submit(true)
assert.equal(state.cooldownSeconds.value, 2)
state.session.value = null
assert.equal(state.cooldownSeconds.value, 0)
assert.equal(tick, undefined)
// 每段间隔必须严格小于 1000ms；软键盘同样追踪，修改永不恢复快速资格。
for (const [first, between, last, modify, expected] of [
  [999, 999, 999, false, 1], [1000, 1, 1, false, 2],
  [1, 1000, 1, false, 2], [1, 1, 1000, false, 2], [1, 1, 1, true, 2],
]) {
  state.session.value = 1; state.item.value = { id: 2 }; state.answered.value = false
  state.content.value = null; state.answer.value = ''; state.resetFluency()
  now += first; state.key('w')
  if (modify) { state.key('Backspace'); state.key('w') }
  now += between; state.key('o'); now += last
  await state.submit()
  assert.equal(state.cooldownSeconds.value, expected)
}
// 原生键盘追加正常；替换、粘贴与 composition 不算一次过。
for (const [inputType, selected, expected] of [['insertText', false, 1], ['insertText', true, 2], ['insertFromPaste', false, 2]]) {
  state.answered.value = false; state.content.value = null; state.answer.value = ''; state.resetFluency()
  now += 1
  state.beforeInput({ target: { value: selected ? 'x' : '', selectionStart: 0, selectionEnd: selected ? 1 : 0 } })
  state.nativeInput({ inputType, data: 'w', target: { value: 'w', selectionStart: 1, selectionEnd: 1 } })
  state.answer.value = 'w'; now += 1; await state.submit()
  assert.equal(state.cooldownSeconds.value, expected)
}
// composition 与多字符自动填充不算快速。
for (const [data, isComposing] of [['w', true], ['word', false]]) {
  state.answered.value = false; state.content.value = null; state.answer.value = ''; state.resetFluency()
  now += 1
  state.nativeInput({ inputType: 'insertText', data, isComposing, target: { value: data, selectionStart: data.length, selectionEnd: data.length } })
  state.answer.value = data; await state.submit()
  assert.equal(state.cooldownSeconds.value, 2)
}
// 错误 3 秒，继续仍取同题并清空答案；刷新恢复巩固不获快速资格。
fetch = async url => url.endsWith('/answer')
  ? Response.json({ word: 'word', correct: false, practice_pending: true })
  : Response.json({ complete: false, item: { id: 2, practice_pending: true } })
state.answered.value = false; state.answer.value = 'bad'; await state.submit()
assert.equal(state.cooldownSeconds.value, 3)
now += 3000; tick(); await state.continueStudy()
assert.equal(state.item.value.id, 2); assert.equal(state.answer.value, '')
assert.equal(state.content.value, null); assert.equal(state.answered.value, false)
const reportRows = [
  { word_id: 1, correct: 1 }, { word_id: 2, correct: null },
  { word_id: 3, correct: 0 }, { word_id: 4, correct: false }, { word_id: 5, correct: true },
]
state.report.value = { words: reportRows }
assert.deepEqual(state.reportWords.value.map(word => word.word_id), [3, 4, 2, 1, 5])
assert.deepEqual(state.report.value.words.map(word => word.word_id), [1, 2, 3, 4, 5], '排序不改变原报告')
assert.match(app, /<table><caption class="sr-only">本轮单词结果，错词优先/)
console.log('study checks passed')
