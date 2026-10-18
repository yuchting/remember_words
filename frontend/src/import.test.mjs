import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

// 运行 App 原有逻辑；只替换挂载钩子，避免访问真实会话或数据库。
const moduleUrl = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`
const utils = moduleUrl(await readFile(new URL('./utils.ts', import.meta.url), 'utf8'))
const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
const script = app.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace('import.meta.env.BASE_URL', "'/words/'")
  .replace('nextTick, onMounted,', 'nextTick,')
  .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
  .replace("from './utils'", `from '${utils}'`)
const state = await import(moduleUrl(`const onMounted = () => {};\n${script}\nexport { source, driveUrl, sheets, selectedSheets, skipped, preview, loadDrive, previewImport, busy };`))
const result = {
  sheets: [
    { name: '第一页', items: [{ word: 'apple', meaning: '苹果' }] },
    { name: '第二页', items: [{ word: 'apple', meaning: '苹果2' }, { word: 'pear', meaning: '梨' }] },
  ],
  skipped: [{ name: '说明页', reason: '缺少 word、meaning 表头' }],
}
let previewCalls = 0
globalThis.fetch = async (url, options) => {
  if (url === '/words/api/import/drive') return Response.json(result)
  assert.equal(url, '/words/api/import/preview')
  previewCalls++
  const { items } = JSON.parse(options.body)
  assert.deepEqual(items, result.sheets.flatMap(sheet => sheet.items))
  return Response.json({ items, duplicates: [{ word: 'apple', indices: [0, 1] }] })
}
state.source.value = 'drive'
state.driveUrl.value = 'https://drive.google.com/test'
await state.loadDrive()
assert.deepEqual(state.skipped.value, result.skipped)
await state.previewImport()
assert.equal(previewCalls, 0, '未选择工作表不能请求预览')
state.selectedSheets.value = [1, 0]
await state.previewImport()
assert.equal(previewCalls, 1)
assert.equal(state.preview.value.items.length, 3)
assert.equal(state.preview.value.duplicates.length, 1)
state.selectedSheets.value = [0]
assert.equal(state.preview.value, null, '选择变化必须清除预览')
state.driveUrl.value = 'https://drive.google.com/changed'
assert.deepEqual(state.selectedSheets.value, [])
assert.deepEqual(state.skipped.value, [])
assert.deepEqual(state.sheets.value, [])

let finish
fetch = () => new Promise(resolve => { finish = resolve })
const loading = state.loadDrive()
state.source.value = 'xlsx'
finish(Response.json(result))
await loading
assert.deepEqual(state.sheets.value, [], '旧解析结果不能覆盖新来源')
assert.deepEqual(state.skipped.value, [])
console.log('工作表多选合并、跨页重复、清理和旧异步结果检查通过')
