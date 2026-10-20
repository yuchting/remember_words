import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { nextTick } from 'vue'
import ts from 'typescript'

// 执行真实组件脚本；保留 Vue 响应式，仅替换 props、生命周期与请求。
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`
const component = await readFile(new URL('./StatisticsPanel.vue', import.meta.url), 'utf8')
const script = component.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace('computed, onBeforeUnmount, onMounted, ref, watch', 'computed, ref, watch')
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
const state = await import(moduleUrl(`
const requests = [];
const defineProps = () => ({
  decks: [{ id: 12, name: '测试词库' }],
  load: path => new Promise((resolve, reject) => requests.push({ path, resolve, reject })),
});
const onMounted = () => {};
let unmount;
const onBeforeUnmount = callback => { unmount = callback };
${script}
export { requests, unmount, refresh, range, deckId, data, loading, error, ratio, percent, stages, mistakes };
`))
const empty = () => ({
  totals: { total: 0, passed: 0, due: 0, stages: {} },
  decks: [], mistakes: [], daily: [], upcoming_due: [],
  first_round: {
    new: { attempts: 0, correct: 0, accuracy: null },
    review: { attempts: 0, correct: 0, accuracy: null },
  },
  new_passed_available: false,
  new_passed_note: '历史新增过关数不可用',
})
// Vue watcher 中的异步 refresh 不由 nextTick 等待；排空 promise 回调。
const settle = () => new Promise(resolve => setImmediate(resolve))

assert.equal(state.requests.length, 0, 'onMounted 替身不自动请求')
const initial = state.refresh()
assert.equal(state.requests[0].path, '/statistics?range=7')
assert.equal(state.loading.value, true)
state.requests[0].resolve(empty())
await initial
assert.equal(state.loading.value, false)
assert.equal(state.error.value, '')
assert.deepEqual(state.stages.value, [])
assert.deepEqual(state.mistakes.value, [])
assert.equal(state.ratio(0, state.data.value.totals.total), 0)
assert.equal(state.percent(state.ratio(0, 0)), '0.0%')
assert.equal(state.percent(state.data.value.first_round.new.accuracy), '—')
assert.equal(state.percent(state.data.value.first_round.review.accuracy), '—')

state.range.value = '30'
state.deckId.value = '12'
await nextTick()
assert.equal(state.requests.length, 2, '同一轮筛选变更合并请求')
assert.equal(state.requests[1].path, '/statistics?range=30&deck_id=12')
assert.equal(state.data.value, null, '筛选变更不展示旧数据')
state.requests[1].resolve(empty())
await settle()
assert.equal(state.loading.value, false)

for (const [failure, message] of [
  [new Error('网络不可用'), '网络不可用'],
  ['未知失败', '统计加载失败，请重试'],
]) {
  const pending = state.refresh()
  state.requests.at(-1).reject(failure)
  await pending
  assert.equal(state.error.value, message)
  assert.equal(state.data.value, null)
  assert.equal(state.loading.value, false)
}
const invalid = state.refresh()
assert.equal(state.error.value, '', '重试清除旧错误')
state.requests.at(-1).resolve({})
await invalid
assert.equal(state.error.value, '统计响应格式无效')
assert.match(component, /role="alert">加载失败：\{\{ error \}\}/)

// 旧成功与旧失败均不能覆盖新结果、错误或 loading 状态。
for (const obsoleteFails of [false, true]) {
  const oldPending = state.refresh()
  const oldRequest = state.requests.at(-1)
  const newPending = state.refresh()
  const newRequest = state.requests.at(-1)
  const latest = empty()
  latest.totals.total = 42
  newRequest.resolve(latest)
  await newPending
  if (obsoleteFails) oldRequest.reject(new Error('过期错误'))
  else oldRequest.resolve(empty())
  await oldPending
  assert.equal(state.data.value.totals.total, 42, '旧响应不覆盖新响应')
  assert.equal(state.error.value, '')
  assert.equal(state.loading.value, false)
}
const oldPending = state.refresh()
const oldRequest = state.requests.at(-1)
const newPending = state.refresh()
const newRequest = state.requests.at(-1)
oldRequest.resolve(empty())
await oldPending
assert.equal(state.loading.value, true, '旧请求结束不能提前关闭新请求 loading')
assert.equal(state.data.value, null)
newRequest.resolve(empty())
await newPending

const pendingUnmount = state.refresh()
const unmountedRequest = state.requests.at(-1)
state.unmount()
unmountedRequest.resolve(empty())
await pendingUnmount
assert.equal(state.data.value, null, '卸载后不写入响应')
assert.equal(state.error.value, '')
console.log('statistics checks passed')
