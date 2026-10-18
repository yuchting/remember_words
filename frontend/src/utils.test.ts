import assert from 'node:assert/strict'
import { deckStatistics, errorMessage, parseImport, reviewComplete } from './utils.ts'

const empty = deckStatistics([], 100)
assert.equal(empty.percentage, 0)
assert.equal(empty.total, 0)
const stats = deckStatistics([{ stage: -1, due_at: null }, { stage: 0, due_at: 100 }, { stage: 5, due_at: 101 }], 100)
assert.deepEqual([stats.total, stats.passed, stats.unlearned, stats.due, stats.percentage], [3, 2, 1, 1, 66.7])
assert.deepEqual(stats.stages.map(stage => stage.count), [1, 1, 0, 0, 0, 0, 1])
assert.equal(deckStatistics([{ stage: 0, due_at: 101 }], 100).percentage, 100)

const groups = [{ indices: [0, 1] }, { indices: [2, 3] }]
assert.equal(reviewComplete(groups, [false, false, false, false], {}), false)
assert.equal(reviewComplete(groups, [true, false, false, true], { 0: '0', 1: '3' }), true)
assert.equal(reviewComplete(groups, [true, true, false, true], { 0: '0', 1: '3' }), false)
assert.equal(reviewComplete(groups, [false, false, false, true], { 0: 'none', 1: '3' }), true)
assert.equal(reviewComplete(groups, [false, true, false, true], { 0: 'none', 1: '3' }), false)

assert.equal(errorMessage({ detail: [{ msg: '密码过短' }] }), '密码过短')
assert.equal(errorMessage({ reason: 'bad' }), '{"reason":"bad"}')
assert.equal(parseImport('[{"word":"apple","meaning":"苹果"}]').length, 1)
for (const text of ['[]', '{}', '[null]', '[{"word":" ","meaning":"苹果"}]']) {
  assert.throws(() => parseImport(text))
}
console.log('导入校验与错误展示检查通过')
