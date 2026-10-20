import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
assert.match(html, /<link rel="manifest" href="%BASE_URL%assets\/manifest\.webmanifest"/)
const url = new URL('../public/assets/manifest.webmanifest', import.meta.url)
const manifest = JSON.parse(readFileSync(url, 'utf8'))
assert.equal(manifest.display, 'standalone')
for (const base of ['https://example.com/', 'https://example.com/words/']) {
  const location = new URL('assets/manifest.webmanifest', base)
  for (const key of ['id', 'start_url', 'scope']) {
    assert.equal(new URL(manifest[key], location).href, base)
  }
  for (const icon of manifest.icons) {
    assert.ok(new URL(icon.src, location).href.startsWith(base + 'assets/'))
  }
}
for (const size of [192, 512]) {
  const icon = manifest.icons.find(icon => icon.sizes === `${size}x${size}`)
  assert.equal(icon?.type, 'image/png')
  const png = readFileSync(new URL(icon.src, url))
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
  assert.equal(png.readUInt32BE(16), size)
  assert.equal(png.readUInt32BE(20), size)
}
console.log('PWA manifest、图标及根路径/子路径检查通过')
