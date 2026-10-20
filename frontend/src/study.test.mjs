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
  .replace("import StatisticsPanel from './StatisticsPanel.vue'", "")
let now = 0
let tick
Object.defineProperty(globalThis, 'performance', { value: { now: () => now }, configurable: true })
globalThis.setInterval = fn => { tick = fn; return 1 }
globalThis.clearInterval = () => { tick = undefined }
globalThis.window = { speechSynthesis: {} }
const spoken = []
globalThis.speechSynthesis = { cancel() {}, speak: value => spoken.push(value.text) }
globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
const state = await import(moduleUrl(`const onMounted = () => {}; const onUnmounted = () => {};\n${script}\nexport { session, item, answer, answered, content, busy, cooldownSeconds, submit, reveal, continueStudy, studyEnter, editingExample, resetFluency, key, beforeInput, nativeInput, report, reportWords, answerInput, speak, clearAudio, resumeStudy, activeStudies, reviewDeckIds, reviewCounts, selectedReviewCount, refresh, start, deckProgress };`))
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
let focused = false
state.answerInput.value = { focus() {
  assert.equal(state.busy.value, false, '只能在输入框解除 busy 禁用后聚焦')
  focused = true
} }
enter({})
await new Promise(resolve => setImmediate(resolve))
assert.equal(nextCalls, 1)
assert.equal(state.answered.value, false)
assert.equal(focused, true, '回车继续后输入框获得焦点')
focused = false
state.answered.value = true
await state.continueStudy()
assert.equal(focused, true, '点击继续同样在加载结束后聚焦')
state.answerInput.value = null
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
// 原生删除后重输、软键盘选区替换，均永久失去本题快速资格。
const freshItem = () => {
  state.session.value = 1; state.item.value = { id: 2 }
  state.answered.value = false; state.content.value = null; state.answer.value = ''
  state.answerInput.value = null; state.resetFluency()
}
fetch = async () => Response.json({ word: 'word', correct: true })
freshItem()
const nativeEdit = (inputType, data, value) => {
  state.beforeInput({ target: { value: state.answer.value, selectionStart: state.answer.value.length, selectionEnd: state.answer.value.length } })
  now += 1
  state.nativeInput({ inputType, data, target: { value, selectionStart: value.length, selectionEnd: value.length } })
  state.answer.value = value
}
nativeEdit('insertText', 'w', 'w')
nativeEdit('deleteContentBackward', null, '')
nativeEdit('insertText', 'w', 'w')
await state.submit()
assert.equal(state.cooldownSeconds.value, 2, '原生删除再输入不恢复快速资格')
freshItem()
now += 1; state.key('w'); now += 1; state.key('o')
state.answerInput.value = { selectionStart: 0, selectionEnd: 1, focus() {}, setSelectionRange() {} }
now += 1; state.key('a')
assert.equal(state.answer.value, 'ao', '软键盘替换选区，不追加')
await state.submit()
assert.equal(state.cooldownSeconds.value, 2, '软键盘替换不算快速')
state.answerInput.value = null

// 真正恢复巩固题；快速输入也只能等 2 秒，拼错仍等 3 秒。
for (const spellingCorrect of [true, false]) {
  let request
  fetch = async (url, options) => {
    if (url.endsWith('/next')) return Response.json({ complete: false, item: { id: 2, practice_pending: true } })
    assert.ok(url.endsWith('/answer'))
    request = JSON.parse(options.body)
    return Response.json({ word: 'word', correct: false, practice: true, spelling_correct: spellingCorrect })
  }
  await state.resumeStudy({ id: 1 })
  now += 1; state.key('w'); now += 1; state.key('o'); now += 1
  await state.submit()
  assert.equal(request.practice, true)
  assert.equal(state.cooldownSeconds.value, spellingCorrect ? 2 : 3)
}

// 1/2/3 秒精确边界；长按 Enter 跨过倒计时不推进，新按一次只取一题。
for (const seconds of [1, 2, 3]) {
  freshItem()
  let calls = 0
  fetch = async url => {
    if (url.endsWith('/answer')) return Response.json({ word: 'word', correct: seconds !== 3 })
    assert.ok(url.endsWith('/next'))
    calls++
    return Response.json({ complete: false, item: { id: 3 } })
  }
  if (seconds === 1) { now += 1; state.key('w') }
  else state.answer.value = 'word'
  await state.submit()
  assert.equal(state.cooldownSeconds.value, seconds)
  const started = now
  enter({})
  for (let elapsed = 999; elapsed < seconds * 1000; elapsed += 1000) {
    now = started + elapsed; tick()
    assert.equal(state.cooldownSeconds.value, seconds - Math.floor(elapsed / 1000))
    enter({ repeat: true })
    await state.continueStudy()
    assert.equal(calls, 0)
  }
  now = started + seconds * 1000; tick()
  assert.equal(state.cooldownSeconds.value, 0)
  enter({ repeat: true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls, 0, '长按跨过截止时间不推进')
  enter({}); enter({ repeat: true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls, 1, '新 Enter 只推进一次')
  assert.equal(state.answered.value, false)
}

// 音频只替换浏览器平台；执行组件真实播放、清理及切题路径。
const realSetTimeout = globalThis.setTimeout
const realClearTimeout = globalThis.clearTimeout
const timers = new Map()
let timerId = 0
const players = []
globalThis.setTimeout = (callback, delay) => {
  const id = ++timerId
  timers.set(id, { callback, at: now + delay })
  return id
}
globalThis.clearTimeout = id => timers.delete(id)
const advance = milliseconds => {
  now += milliseconds
  for (const [id, timer] of timers) {
    if (timer.at <= now) { timers.delete(id); timer.callback() }
  }
}
globalThis.Audio = class {
  constructor(src) {
    this.src = src; this.paused = false; this.loaded = false
    this.onplaying = null; this.onerror = null
    this.pending = new Promise((resolve, reject) => { this.reject = reject })
    players.push(this)
  }
  play() { return this.pending }
  pause() { this.paused = true }
  removeAttribute(name) { assert.equal(name, 'src'); this.src = null }
  load() { this.loaded = true }
}
const stopped = player => {
  assert.equal(player.paused, true)
  assert.equal(player.src, null)
  assert.equal(player.loaded, true)
  assert.equal(player.onerror, null)
  assert.equal(player.onplaying, null)
}
try {
  for (const failure of ['reject', 'error', 'timeout']) {
    const count = spoken.length
    state.speak(failure, '/word.mp3')
    const player = players.at(-1)
    const lateError = player.onerror
    const lateTimeout = [...timers.values()][0].callback
    if (failure === 'reject') {
      player.reject(new Error('autoplay blocked'))
      await Promise.resolve()
    } else if (failure === 'error') player.onerror()
    else {
      advance(7999)
      assert.equal(spoken.length, count, '8000ms 前不回退')
      advance(1)
    }
    assert.deepEqual(spoken.slice(count), [failure])
    stopped(player)
    assert.equal(timers.size, 0)
    lateError(); lateTimeout(); advance(8000)
    assert.equal(spoken.length, count + 1, '同一失败只回退一次')
  }
  const count = spoken.length
  state.speak('playing', '/word.mp3')
  const playing = players.at(-1)
  advance(7999); playing.onplaying()
  assert.equal(timers.size, 0, 'onplaying 取消加载超时')
  advance(8001)
  assert.equal(spoken.length, count, '已开始播放不因原超时回退')
  assert.equal(playing.paused, false)
  state.clearAudio(); stopped(playing)

  for (const cleanup of ['clear', 'next']) {
    state.speak('obsolete', '/old.mp3')
    const old = players.at(-1)
    const lateError = old.onerror
    const lateTimeout = [...timers.values()][0].callback
    if (cleanup === 'clear') state.clearAudio()
    else {
      state.answered.value = true
      await state.continueStudy()
    }
    stopped(old)
    assert.equal(timers.size, 0, '清理或切题取消超时')
    state.speak('current', '/new.mp3')
    const current = players.at(-1)
    const before = spoken.length
    lateError(); lateTimeout(); old.reject(new Error('late rejection'))
    await Promise.resolve()
    assert.equal(spoken.length, before, '旧错误、超时、拒绝不重播')
    assert.equal(current.paused, false, '旧回调不停止新音频')
    assert.equal(timers.size, 1, '新音频仍保留自己的超时')
    state.clearAudio(); stopped(current)
  }
} finally {
  state.clearAudio()
  globalThis.setTimeout = realSetTimeout
  globalThis.clearTimeout = realClearTimeout
  delete globalThis.Audio
}
const reportRows = [
  { word_id: 1, correct: 1 }, { word_id: 2, correct: null },
  { word_id: 3, correct: 0 }, { word_id: 4, correct: false }, { word_id: 5, correct: true },
]
state.report.value = { words: reportRows }
assert.deepEqual(state.reportWords.value.map(word => word.word_id), [3, 4, 2, 1, 5])
assert.deepEqual(state.report.value.words.map(word => word.word_id), [1, 2, 3, 4, 5], '排序不改变原报告')
assert.match(app, /<table><caption class="sr-only">本轮单词结果，错词优先/)
// 多词库独立恢复；复习请求只发送所选词库，刷新后保留全部批次。
const progressA = { id: 101, deck_id: 1, round_number: 3, total: 4, answered: 2 }
const progressB = { id: 102, deck_id: 2, round_number: 1, total: 6, answered: 0 }
let selection
fetch = async (url, options) => {
  if (url.endsWith('/reviews/study')) {
    selection = JSON.parse(options.body)
    return Response.json({ id: 102, sessions: [progressB] })
  }
  if (url.endsWith('/decks')) return Response.json([{ id: 1, name: 'A' }, { id: 2, name: 'B' }])
  if (url.endsWith('/reviews/due')) return Response.json({ count: 6, decks: [{ deck_id: 1, count: 0 }, { deck_id: 2, count: 6 }] })
  if (url.endsWith('/study/active')) return Response.json({ sessions: [progressA, progressB] })
  if (url.endsWith('/next')) return Response.json({ complete: false, item: { id: 99 } })
  throw new Error(`Unexpected URL: ${url}`)
}
state.reviewDeckIds.value = [2, 999]
await state.refresh()
assert.deepEqual(state.reviewDeckIds.value, [2])
assert.equal(state.selectedReviewCount.value, 6)
assert.equal(state.deckProgress(1).round_number, 3)
await state.resumeStudy(progressA)
assert.equal(state.session.value, 101)
await state.resumeStudy(progressB)
assert.equal(state.session.value, 102)
await state.start()
assert.deepEqual(selection, { deck_ids: [2] })
assert.equal(state.session.value, 102)
assert.equal(state.activeStudies.value.length, 2)
console.log('study checks passed')
