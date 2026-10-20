// Run with Vite on LAYOUT_URL; Playwright installed separately, not an app dependency.
// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node frontend/src/layout.test.mjs
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch()
try {
  const page = await browser.newPage()
  let authenticated = true
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let value = []
    if (path.endsWith('/auth/me')) {
      if (!authenticated) return route.fulfill({ status: 401, json: { detail: '请先登录' } })
      value = { id: 1, role: 'parent', username: 'parent-layout-check', csrf_token: 'test' }
    }
    if (path.endsWith('/decks')) value = [{ id: 1, name: '小学英语单词与例句练习', total: 100, learned: 20, due: 10 }]
    if (path.endsWith('/reviews/due')) value = { count: 10, decks: [{ deck_id: 1, count: 10 }] }
    if (path.endsWith('/study/active')) value = { session: null, sessions: [] }
    if (path.endsWith('/admin/speech')) value = { primary: 'azure', fallback: 'google', browser_fallback: false, azure: { region: 'eastasia', voice: 'en-US-JennyNeural', key_set: true }, google: { voice: 'en-US-Neural2-F', key_set: false } }
    if (path.endsWith('/speech/cache')) value = { files: 1234, bytes: 5345280, max_bytes: 104857600 }
    if (path.endsWith('/speech/settings')) value = { enabled: false, browser_fallback: false }
    if (path.endsWith('/statistics')) value = { totals: { total: 100, passed: 20, due: 10, stages: { '-1': 80, '0': 20 } }, decks: [{ id: 1, name: '小学英语', total: 100, passed: 20, due: 10 }], mistakes: [], first_round: { new: { attempts: 0, correct: 0, accuracy: null }, review: { attempts: 0, correct: 0, accuracy: null } }, daily: [], upcoming_due: [], new_passed_available: false, new_passed_note: '历史未记录过关时间' }
    if (path.endsWith('/study') && route.request().method() === 'POST') value = { id: 1 }
    if (path.endsWith('/next')) value = { complete: false, item: { id: 1, round_number: 1, answered: 0, total: 100, remaining: 100, meaning: '非常美好的；精彩的；令人赞叹的' } }
    if (path.endsWith('/reveal')) value = { word: 'wonderful', meaning: '非常美好的；精彩的；令人赞叹的', phonetic: '/ˈwʌndəfəl/', word_id: 1, example: 'We had a wonderful time learning English together.', example_translation: '我们一起学习英语，度过了美好的时光。' }
    await route.fulfill({ json: value })
  })
  async function check(name, width, height) {
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}x${height} ${name}: no page overflow`)
    for (const button of await page.locator('main button:visible').all()) {
      if (await button.evaluate(el => !!el.closest('.table-scroll'))) continue
      const box = await button.boundingBox()
      assert.ok(box.x >= 0 && box.x + box.width <= width + 1, `${name}: button within viewport`)
    }
    if (process.env.LAYOUT_SCREENSHOTS) await page.screenshot({ path: `${process.env.LAYOUT_SCREENSHOTS}/${width}x${height}-${name}.png`, fullPage: true })
  }
  for (const [width, height] of [[720, 480], [1280, 720], [480, 720], [800, 600]]) {
    await page.setViewportSize({ width, height })
    authenticated = false
    await page.goto(process.env.LAYOUT_URL || 'http://127.0.0.1:19387')
    await page.getByRole('button', { name: '登录', exact: true }).waitFor()
    await check('login', width, height)
    authenticated = true
    await page.reload()
    await page.getByRole('button', { name: '云端朗读', exact: true }).waitFor()
    await check('decks', width, height)
    for (const [label, name] of [['总体统计', 'statistics'], ['云端朗读', 'speech'], ['导入词库', 'import'], ['个人备份', 'backup'], ['家庭管理', 'family']]) {
      await page.getByRole('button', { name: label, exact: true }).first().click()
      await check(name, width, height)
    }
    await page.getByRole('button', { name: '我的词库', exact: true }).click()
    await page.getByRole('button', { name: '开始学习', exact: false }).last().click()
    await page.getByRole('button', { name: '揭示答案', exact: true }).waitFor()
    await check('study', width, height)
    if (width >= 681) {
      const submit = await page.getByRole('button', { name: '提交答案', exact: true }).boundingBox()
      assert.ok(submit.y + submit.height <= height, 'landscape submit button visible without scroll')
    }
    assert.equal(await page.locator('.keyboard').count(), 0, 'soft keyboard hidden by default')
    const toggle = page.getByRole('button', { name: '展开软键盘', exact: true })
    await toggle.click()
    await page.getByRole('button', { name: 'q', exact: true }).click()
    assert.equal(await page.locator('#answer').inputValue(), 'q')
    await check('keyboard', width, height)
    await page.getByRole('button', { name: '揭示答案', exact: true }).click()
    await page.getByRole('button', { name: '朗读例句', exact: true }).waitFor()
    await check('answer', width, height)
  }
  console.log('Chromium responsive layout checks passed: 720x480, 1280x720, 480x720, 800x600 (mock API)')
} finally {
  await browser.close()
}
