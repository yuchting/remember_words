import assert from 'node:assert/strict'
import { errorMessage, parseImport } from './utils.ts'

assert.equal(errorMessage({ detail: [{ msg: '密码过短' }] }), '密码过短')
assert.equal(errorMessage({ reason: 'bad' }), '{"reason":"bad"}')
assert.equal(parseImport('[{"word":"apple","meaning":"苹果"}]').length, 1)
for (const text of ['[]', '{}', '[null]', '[{"word":" ","meaning":"苹果"}]']) {
  assert.throws(() => parseImport(text))
}
console.log('导入校验与错误展示检查通过')
