<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { errorMessage, parseImport, reviewComplete } from './utils'

type User = { id: number; username: string; role: string; is_active?: boolean | number }
type Deck = { id: number; name: string; total: number; due: number; learned?: number; started_at?: string }
type Item = { id: number; meaning: string; round_number: number; remaining: number }
type Content = { word: string; meaning: string; example?: string; example_translation?: string; audio?: string; phonetic?: string; correct?: boolean; complete?: boolean; word_id?: number; definitions?: string[] }
type Summary = { attempts: number; correct: number }
type ImportWord = { word: string; meaning: string; example?: string; example_translation?: string }
type Preview = { items: ImportWord[]; duplicates: { word: string; indices: number[] }[] }
type Sheet = { name: string; items: ImportWord[] }
type SheetResult = { sheets: Sheet[]; skipped: { name: string; reason: string }[] }
type ManagedWord = ImportWord & { id: number; stage: number; due_at: number | null; attempts: number; correct: number; last_answer_at: number | null }
type DeckDetail = { id: number; name: string; words: ManagedWord[] }
type History = { stage: number; due_at: number | null; intervals: number[]; attempts: { correct: boolean; created_at: number; answer: string; round_number: number }[] }
const user = ref<User | null>(null)
const ready = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let csrf = ''
const page = ref('decks')
const username = ref('')
const password = ref('')
const decks = ref<Deck[]>([])
const users = ref<User[]>([])
const due = ref(0)
const deckName = ref('')
const importText = ref('')
const targetUser = ref('')
const source = ref('json')
const driveUrl = ref('')
const sheets = ref<Sheet[]>([])
const selectedSheets = ref<number[]>([])
const skipped = ref<SheetResult['skipped']>([])
const preview = ref<Preview | null>(null)
const included = ref<boolean[]>([])
const groupChoices = ref<Record<number, string>>({})
let sourceVersion = 0
function clearPreview() { sourceVersion++; preview.value = null; included.value = []; groupChoices.value = {} }
function clearSheets() { clearPreview(); sheets.value = []; selectedSheets.value = []; skipped.value = [] }
watch([importText, driveUrl, source], clearSheets, { flush: 'sync' })
watch([selectedSheets, targetUser], clearPreview, { flush: 'sync', deep: true })
const selectedItems = computed(() => preview.value?.items.filter((_, i) => included.value[i]) ?? [])
const canImport = computed(() => !!preview.value && reviewComplete(preview.value.duplicates, included.value, groupChoices.value))
function chooseGroup(groupIndex: number) {
  const group = preview.value?.duplicates[groupIndex]
  if (group) for (const i of group.indices) included.value[i] = groupChoices.value[groupIndex] === String(i)
}
const detail = ref<DeckDetail | null>(null)
const rename = ref('')
const wordDraft = ref<ManagedWord | null>(null)
const history = ref<History | null>(null)
const historyWord = ref('')
const dateTime = (time: number | null) => time === null ? '暂无' : new Date(time * 1000).toLocaleString('zh-CN')
const intervalLabel = (seconds: number) => seconds < 86400 ? `${seconds / 60} 分钟` : `${seconds / 86400} 天`
const childName = ref('')
const childPassword = ref('')
const resetPasswords = ref<Record<number, string>>({})
const session = ref<number | null>(null)
const item = ref<Item | null>(null)
const content = ref<Content | null>(null)
const summary = ref<Summary | null>(null)
const answer = ref('')
const answered = ref(false)
const editingExample = ref(false)
const exampleDraft = ref('')
const translationDraft = ref('')
const answerInput = ref<HTMLInputElement | null>(null)
const keyboard = ref(true)
const isParent = computed(() => user.value?.role === 'parent' || user.value?.role === 'admin')
const totalWords = computed(() => decks.value.reduce((sum, deck) => sum + deck.total, 0))
const rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']

async function api<T>(path: string, method = 'GET', body?: unknown, mode?: 'raw' | 'download'): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin',
    headers: { ...(body === undefined ? {} : { 'Content-Type': mode === 'raw' ? 'application/octet-stream' : 'application/json' }), ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrf }) },
    body: body === undefined ? undefined : mode === 'raw' ? body as Blob : JSON.stringify(body),
  })
  if (response.ok && mode === 'download') return await response.blob() as T
  const data = await response.json().catch(() => null)
  if (data?.csrf_token) csrf = data.csrf_token
  if (!response.ok) {
    if (response.status === 401) { user.value = null; session.value = null }
    throw new Error(data ? errorMessage(data.detail ?? data) : `请求失败（${response.status}）`)
  }
  return data as T
}
async function run(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action() } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试' }
  finally { busy.value = false }
}
async function refresh() {
  const [list, review] = await Promise.all([api<Deck[]>('/decks'), api<{ count: number }>('/reviews/due')])
  decks.value = list; due.value = review.count
  if (isParent.value) users.value = await api<User[]>('/admin/users')
}
async function login() {
  await run(async () => {
    const result = await api<User & { user?: User }>('/auth/login', 'POST', { username: username.value, password: password.value })
    user.value = result.user ?? (result.id ? result : await api<User>('/auth/me'))
    password.value = ''; await refresh()
  })
}
async function logout() {
  await run(async () => { await api('/auth/logout', 'POST'); user.value = null; csrf = ''; session.value = null; page.value = 'decks'; content.value = null; summary.value = null; detail.value = null; wordDraft.value = null; history.value = null; clearSheets(); importText.value = ''; driveUrl.value = ''; targetUser.value = '' })
}
async function createChild() {
  await run(async () => {
    await api('/admin/users', 'POST', { username: childName.value, password: childPassword.value })
    childName.value = ''; childPassword.value = ''; await refresh(); notice.value = '孩子账号已创建'
  })
}
async function updateChild(child: User, reset: boolean) {
  if (reset && !resetPasswords.value[child.id]) { error.value = '请输入新密码'; return }
  if (!reset && !confirm(`确定${(child.is_active === false || child.is_active === 0) ? '启用' : '停用'}「${child.username}」？`)) return
  await run(async () => {
    await api(`/admin/users/${child.id}`, 'PATCH', reset ? { password: resetPasswords.value[child.id] } : { is_active: (child.is_active === false || child.is_active === 0) })
    delete resetPasswords.value[child.id]; await refresh(); notice.value = reset ? '密码已重置' : '账号状态已更新'
  })
}
async function readFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  clearSheets()
  if (!file) return
  const version = sourceVersion
  await run(async () => {
    if (source.value === 'json') {
      if (file.size > 2 * 1024 * 1024) throw new Error('JSON 文件不能超过 2 MiB')
      const text = await file.text()
      if (version === sourceVersion) importText.value = text
    } else {
      const result = await api<SheetResult>('/import/xlsx', 'POST', file, 'raw')
      if (version === sourceVersion) { sheets.value = result.sheets; skipped.value = result.skipped }
    }
  })
  input.value = ''
}
async function loadDrive() {
  clearSheets()
  const version = sourceVersion
  await run(async () => {
    const result = await api<SheetResult>('/import/drive', 'POST', { url: driveUrl.value })
    if (version === sourceVersion) { sheets.value = result.sheets; skipped.value = result.skipped }
  })
}
async function previewImport() {
  clearPreview()
  const version = sourceVersion
  await run(async () => {
    if (source.value !== 'json' && !selectedSheets.value.length) throw new Error('请先选择工作表')
    const items = source.value === 'json' ? parseImport(importText.value) : sheets.value.flatMap((sheet, i) => selectedSheets.value.includes(i) ? sheet.items : [])
    const result = await api<Preview>('/import/preview', 'POST', { items })
    if (version !== sourceVersion) return
    preview.value = result
    const duplicates = new Set(result.duplicates.flatMap(group => group.indices))
    included.value = result.items.map((_, i) => !duplicates.has(i))
  })
}
async function importDeck() {
  if (!canImport.value) { error.value = '请先预览，并逐组选择保留一条或全排除'; return }
  await run(async () => {
    const payload = { items: selectedItems.value, ...(deckName.value.trim() ? { name: deckName.value.trim() } : {}), ...(isParent.value && targetUser.value ? { user_id: Number(targetUser.value) } : {}) }
    if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > 2 * 1024 * 1024) throw new Error('导入请求不能超过 2 MiB，请拆分词库')
    await api('/decks/import', 'POST', payload)
    clearSheets(); importText.value = ''; deckName.value = ''; await refresh(); notice.value = '词库导入成功'; page.value = 'decks'
  })
}
async function openDeck(id: number) {
  await run(async () => {
    detail.value = await api<DeckDetail>(`/decks/${id}`)
    rename.value = detail.value.name; wordDraft.value = null; history.value = null; page.value = 'manage'
  })
}
async function reloadDetail() {
  if (detail.value) detail.value = await api<DeckDetail>(`/decks/${detail.value.id}`)
  await refresh()
}
async function renameDeck() {
  if (!detail.value || !confirm('确定修改词库名称？')) return
  await run(async () => { await api(`/decks/${detail.value!.id}`, 'PATCH', { name: rename.value.trim() }); await reloadDetail() })
}
async function deleteDeck() {
  if (!detail.value || !confirm(`确定删除词库「${detail.value.name}」及其全部词条和历史？会结束包含这些词条的活动批次。此操作不可撤销。`)) return
  await run(async () => { await api(`/decks/${detail.value!.id}`, 'DELETE'); detail.value = null; page.value = 'decks'; await refresh() })
}
async function exportDeck() {
  if (!detail.value) return
  await run(async () => {
    const blob = await api<Blob>(`/decks/${detail.value!.id}/export`, 'GET', undefined, 'download')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = `${detail.value!.name}.json`
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}
async function saveWord() {
  if (!wordDraft.value || !confirm('确定修改词条？会结束包含该词条的活动批次。')) return
  await run(async () => {
    const { id, meaning, example, example_translation } = wordDraft.value!
    await api(`/words/${id}`, 'PATCH', { meaning, example: example ?? '', example_translation: example_translation ?? '' })
    wordDraft.value = null; history.value = null; await reloadDetail()
  })
}
async function changeWord(word: ManagedWord, reset: boolean) {
  if (!confirm(reset ? `确定重置「${word.word}」？会删除该词全部答题历史并重置复习进度，结束包含该词条的活动批次。此操作不可撤销。` : `确定删除「${word.word}」及其历史？会结束包含该词条的活动批次。此操作不可撤销。`)) return
  await run(async () => {
    await api(reset ? `/words/${word.id}/reset` : `/words/${word.id}`, reset ? 'POST' : 'DELETE')
    wordDraft.value = null; history.value = null; await reloadDetail()
  })
}
async function showHistory(word: ManagedWord) {
  await run(async () => { history.value = await api<History>(`/words/${word.id}/history`); historyWord.value = word.word })
}
async function loadNext() {
  clearAudio()
  const data = await api<{ complete: boolean; item?: Item; summary?: Summary }>(`/study/${session.value}/next`)
  item.value = data.item ?? null; summary.value = data.complete ? (data.summary ?? { attempts: 0, correct: 0 }) : null
  content.value = null; answer.value = ''; answered.value = false; editingExample.value = false
  if (data.complete) await refresh()
  await nextTick(); answerInput.value?.focus()
}
async function start(deck?: Deck) {
  await run(async () => {
    const data = await api<{ id: number }>(deck ? `/decks/${deck.id}/study` : '/reviews/study', 'POST')
    session.value = data.id; summary.value = null; item.value = null; content.value = null; await loadNext()
  })
}
async function reveal() {
  await run(async () => { content.value = await api<Content>(`/study/${session.value}/items/${item.value?.id}/reveal`, 'POST') })
}
async function submit(skip = false) {
  if (answered.value || !item.value || (!skip && !answer.value.trim())) return
  await run(async () => {
    content.value = await api<Content>(`/study/${session.value}/items/${item.value?.id}/answer`, 'POST', { answer: answer.value.trim(), skip })
    answered.value = true
  })
}
async function enrich() {
  if (!content.value?.word_id) return
  await run(async () => {
    const updated = await api<Content & { notice?: string }>(`/words/${content.value!.word_id}/enrich`, 'POST')
    content.value = { ...content.value!, ...updated }
    notice.value = updated.notice ?? '已更新词典内容'
  })
}
function editExample() {
  exampleDraft.value = content.value?.example ?? ''
  translationDraft.value = content.value?.example_translation ?? ''
  editingExample.value = true
}
async function saveExample() {
  if (!content.value?.word_id) return
  await run(async () => {
    const fields = { example: exampleDraft.value.trim(), example_translation: translationDraft.value.trim() }
    if (!fields.example || !fields.example_translation) throw new Error('请填写完整英文例句和中文译文')
    if (!confirm('确定修改例句和译文？会结束包含该词条的活动批次。保存后请返回词库重新开始学习。')) return
    await api(`/words/${content.value!.word_id}`, 'PATCH', fields)
    content.value = { ...content.value!, ...fields }; editingExample.value = false; notice.value = '例句和译文已保存'
  })
}
function key(value: string) {
  const input = answerInput.value
  const start = input?.selectionStart ?? answer.value.length
  const end = input?.selectionEnd ?? start
  const from = value === 'Backspace' && start === end ? Math.max(0, start - 1) : start
  answer.value = answer.value.slice(0, from) + (value === 'Backspace' ? '' : value) + answer.value.slice(end)
  nextTick(() => { input?.focus(); const position = from + (value === 'Backspace' ? 0 : value.length); input?.setSelectionRange(position, position) })
}
let activeAudio: HTMLAudioElement | null = null
let audioTimer: ReturnType<typeof setTimeout> | undefined
function clearAudio() {
  clearTimeout(audioTimer); audioTimer = undefined
  if (activeAudio) {
    activeAudio.onplaying = null; activeAudio.onerror = null
    activeAudio.pause(); activeAudio.removeAttribute('src'); activeAudio.load(); activeAudio = null
  }
  if ('speechSynthesis' in window) speechSynthesis.cancel()
}
function speak(text: string, audio?: string) {
  clearAudio()
  if (audio && /^(https?:\/\/|\/)/i.test(audio) && !audio.startsWith('//')) {
    const player = new Audio(audio)
    activeAudio = player
    const fallback = () => { if (activeAudio === player) speak(text) }
    player.onplaying = () => { clearTimeout(audioTimer); audioTimer = undefined }
    player.onerror = fallback
    audioTimer = setTimeout(fallback, 8000)
    player.play().catch(fallback); return
  }
  if (!('speechSynthesis' in window)) { error.value = '浏览器不支持语音播放'; return }
  speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'en-US'; utterance.rate = 0.85
  utterance.onerror = () => { error.value = '语音播放失败，请检查浏览器语音设置' }
  speechSynthesis.speak(utterance)
}
async function leaveStudy() {
  clearAudio()
  await run(async () => { await refresh(); session.value = null; item.value = null; content.value = null; summary.value = null })
}
onMounted(async () => {
  try { user.value = await api<User>('/auth/me'); await refresh() }
  catch (cause) { if (user.value) error.value = cause instanceof Error ? cause.message : '加载失败' }
  finally { ready.value = true }
})
</script>

<template>
  <div v-if="!ready" class="loading" role="status">词间 · 正在准备今天的学习…</div>
  <template v-else>
    <header class="header">
      <a class="brand" href="#" @click.prevent="session ? leaveStudy() : page = 'decks'"><span class="brand-mark">词</span><span>词间<small>每天记一点，慢慢走很远</small></span></a>
      <div v-if="user" class="account"><span>{{ user.username }} <small>{{ isParent ? '家长' : '学习者' }}</small></span><button class="text-button" :disabled="busy" @click="logout">退出</button></div>
      <span v-else class="header-note">让每个单词，成为老朋友</span>
    </header>
    <div v-if="error" class="message error" role="alert">{{ error }}<button aria-label="关闭错误提示" @click="error = ''">×</button></div>
    <div v-if="notice" class="message success" role="status">{{ notice }}</div>
    <main v-if="!user" class="login-layout">
      <section class="login-intro"><p class="eyebrow">一点积累 · 一点成长</p><h1>记住单词，<br>也记住进步。</h1><p>看中文，想英文。用一次次主动回忆，<br>把短暂的记忆变成长久的掌握。</p><div class="word-note"><span>grow</span><small>/ɡrəʊ/</small><strong>成长；逐渐成为</strong><p>Small steps, every day.</p></div></section>
      <form class="panel login-form" @submit.prevent="login"><p class="eyebrow">欢迎回来</p><h2>开始今天的学习</h2><p class="muted">使用你的账号登录，接着上次的进度。</p><label>用户名<input v-model="username" autocomplete="username" required maxlength="150" placeholder="请输入用户名" /></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required placeholder="请输入密码" /></label><button class="primary wide" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button><p class="fine">孩子账号由家长创建。忘记密码？请联系家长重置。</p></form>
    </main>
    <main v-else-if="session" class="study-layout">
      <div class="section-heading"><button class="text-button" :disabled="busy" @click="leaveStudy">← 返回词库</button><span class="muted">{{ summary ? '本轮完成' : `第 ${item?.round_number ?? 1} 轮 · 剩余 ${item?.remaining ?? '…'} 词` }}</span></div>
      <section v-if="summary" class="panel result-panel"><span class="result-mark">✓</span><p class="eyebrow">每次回忆，都算进步</p><h1>这一轮，完成了！</h1><div class="result-stats"><div><strong>{{ summary.attempts }}</strong><span>回答次数</span></div><div><strong>{{ summary.correct }}</strong><span>正确次数</span></div><div><strong>{{ summary.attempts ? Math.round(summary.correct / summary.attempts * 100) : 0 }}%</strong><span>正确率</span></div></div><p class="muted">让记忆休息一下，到期后再来巩固。</p><button class="primary" :disabled="busy" @click="leaveStudy">回到词库</button></section>
      <section v-else-if="item" class="panel study-panel">
        <p class="eyebrow">看中文 · 回忆英文</p><h1 class="meaning">{{ item.meaning }}</h1>
        <form @submit.prevent="submit()"><label class="sr-only" for="answer">输入英文单词</label><input id="answer" ref="answerInput" v-model="answer" class="answer-input" lang="en" autocomplete="off" autocapitalize="none" :spellcheck="false" :disabled="busy || answered" placeholder="在这里输入英文" /><div class="study-actions"><button type="button" class="secondary" :disabled="busy || answered || !!content" @click="reveal">揭示答案</button><button type="button" class="secondary" :disabled="busy || answered" @click="submit(true)">不会，记一下</button><button class="primary" :disabled="busy || answered || !answer.trim()">提交答案</button></div></form>
        <div v-if="content" class="answer-card" aria-live="polite"><p v-if="answered" :class="['answer-status', content.correct ? 'correct' : 'incorrect']">{{ content.correct ? '回答正确，记得不错！' : '还没记牢，再认识一次。' }}</p><div class="word-line"><h2>{{ content.word }}</h2><span class="muted">{{ content.phonetic }}</span><button class="audio-button" aria-label="播放单词发音" @click="speak(content.word, content.audio)">朗读单词</button></div><p>{{ content.meaning }}</p><ul v-if="content.definitions?.length" lang="en"><li v-for="definition in content.definitions.slice(0, 5)" :key="definition">{{ definition }}</li></ul><template v-if="content.example"><div class="example-line"><p lang="en">{{ content.example }}</p><button class="text-button" @click="speak(content.example!)">朗读例句</button></div><p class="muted">{{ content.example_translation }}</p></template><p v-else class="muted">暂无例句，可查词补全或手工添加。</p><div v-if="content.word_id" class="content-tools"><button class="secondary" :disabled="busy || editingExample" @click="enrich">查词补全</button><button class="text-button" :disabled="busy" @click="editExample">编辑例句与译文</button></div><form v-if="editingExample" class="example-editor" @submit.prevent="saveExample"><label>完整英文例句<textarea v-model="exampleDraft" lang="en" required maxlength="2000" rows="3" placeholder="填写完整英文句子"></textarea></label><label>完整中文译文<textarea v-model="translationDraft" required maxlength="2000" rows="3" placeholder="填写对应中文译文"></textarea></label><div class="content-tools"><button class="primary" :disabled="busy">保存例句与译文</button><button type="button" class="text-button" :disabled="busy" @click="editingExample = false">取消</button></div></form><p v-if="!answered" class="fine">看过答案也没关系，输入一遍加深印象，或选择“不会”。</p><button v-if="answered" class="primary wide" :disabled="busy" @click="run(loadNext)">{{ busy ? '正在加载…' : '继续 →' }}</button></div>
        <div v-if="!answered" class="keyboard-wrap"><button class="text-button keyboard-toggle" :aria-expanded="keyboard" @click="keyboard = !keyboard">{{ keyboard ? '收起软键盘' : '展开软键盘' }}</button><div v-if="keyboard" class="keyboard" aria-label="英文软键盘"><div v-for="row in rows" :key="row" class="keyboard-row"><button v-for="letter in row" :key="letter" :disabled="busy" @mousedown.prevent @click="key(letter)">{{ letter }}</button></div><div class="keyboard-row"><button :disabled="busy" @mousedown.prevent @click="key('-')">-</button><button :disabled="busy" @mousedown.prevent @click="key('\'')">'</button><button class="space-key" :disabled="busy" @mousedown.prevent @click="key(' ')">空格</button><button class="delete-key" aria-label="删除一个字符" :disabled="busy" @mousedown.prevent @click="key('Backspace')">⌫</button></div></div></div>
      </section>
      <section v-else class="panel"><p>学习内容暂未载入。</p><button class="primary" :disabled="busy" @click="run(loadNext)">重新加载</button></section>
    </main>
    <main v-else class="dashboard">
      <nav class="tabs" aria-label="主要导航"><button :class="{ active: page === 'decks' }" @click="page = 'decks'">我的词库</button><button :class="{ active: page === 'import' }" @click="page = 'import'">导入词库</button><button v-if="isParent" :class="{ active: page === 'family' }" @click="page = 'family'">家庭管理</button></nav>
      <template v-if="page === 'decks'">
        <section class="welcome"><div><p class="eyebrow">把今天的一点，变成明天的更多</p><h1>你好，{{ user.username }}。</h1><p class="muted">不必一次记住所有，只要每天向前一点。</p><p class="fine">每人同时进行一个学习批次；开始任意词库会优先恢复未完成的批次。</p></div><div class="today-date">{{ new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }) }}</div></section>
        <section class="review-banner"><div><span class="eyebrow">今日复习</span><h2>{{ due ? `${due} 个单词，等你再见一面` : '今天的复习，已安排妥当' }}</h2><p>{{ due ? '趁记忆还在，给它一点巩固。' : '暂时没有到期单词，挑一个词库开始学习吧。' }}</p></div><button :disabled="busy || !due" @click="start()">开始复习 <span>→</span></button></section>
        <div class="section-heading"><h2>我的词库 <span class="count">{{ decks.length }}</span></h2><span class="muted">共 {{ totalWords }} 个单词</span></div>
        <div v-if="decks.length" class="deck-grid"><article v-for="(deck, index) in decks" :key="deck.id" class="panel deck-card"><span class="deck-icon" :class="`tone-${index % 3}`">{{ deck.name?.slice(0, 1) || '词' }}</span><h3>{{ deck.name || '未命名词库' }}</h3><p class="muted">{{ deck.total }} 个单词 <span class="dot">·</span> 已掌握 {{ deck.learned ?? 0 }} 个 <span class="dot">·</span> {{ deck.due }} 个待复习</p><button class="secondary" :disabled="busy" @click="openDeck(deck.id)">管理词库</button><div class="deck-footer"><span class="fine">{{ deck.started_at ? '已开始学习' : '等待初次相遇' }}</span><button class="text-button" :disabled="busy || !deck.total" @click="start(deck)">{{ deck.started_at ? '继续学习' : '开始学习' }} →</button></div></article></div>
        <section v-else class="panel empty"><h3>第一本词库，从这里开始</h3><p class="muted">导入一份 JSON 单词表，开启你的记忆旅程。</p><button class="primary" @click="page = 'import'">导入词库</button></section>
      </template>
      <section v-else-if="page === 'import'" class="panel import-panel">
        <p class="eyebrow">准备好你的单词</p><h1>导入词库</h1><p class="muted">先解析，再逐行检查预览，最后确认导入。重复单词（同义也算）须逐组选择。</p>
        <div class="form-grid"><label>词库名称（可选）<input v-model="deckName" maxlength="100" :disabled="busy" /></label><label v-if="isParent">导入给谁<select v-model="targetUser" :disabled="busy"><option value="">我自己</option><option v-for="child in users.filter(u => u.id !== user?.id && u.is_active !== false && u.is_active !== 0)" :key="child.id" :value="String(child.id)">{{ child.username }}</option></select></label></div>
        <label>来源<select v-model="source" :disabled="busy"><option value="json">JSON</option><option value="xlsx">Excel（.xlsx）</option><option value="drive">Google Drive 链接</option></select></label>
        <template v-if="source === 'json'"><label class="file-picker">选择 JSON 文件（最大 2 MiB）<input type="file" accept=".json,application/json" :disabled="busy" @change="readFile" /></label><label>JSON 内容<textarea v-model="importText" rows="8" :disabled="busy" spellcheck="false" placeholder='[{"word":"apple","meaning":"苹果"}]'></textarea></label></template>
        <label v-else-if="source === 'xlsx'" class="file-picker">选择 Excel 文件<input type="file" accept=".xlsx" :disabled="busy" @change="readFile" /></label>
        <form v-else @submit.prevent="loadDrive"><label>Google Drive / Google Sheets 链接<input v-model="driveUrl" type="url" required :disabled="busy" placeholder="https://drive.google.com/…" /></label><p class="fine">链接需允许服务读取。仅解析内容，不会修改原文件。</p><button class="secondary" :disabled="busy || !driveUrl.trim()">解析链接</button></form>
        <fieldset v-if="sheets.length" :disabled="busy" class="duplicate-group"><legend>选择工作表</legend><p class="fine">选中页合并为一个词库；跨页重复单词须在预览中逐组处理。</p><div class="content-tools"><button type="button" class="text-button" @click="selectedSheets = sheets.map((_, i) => i)">全选</button><button type="button" class="text-button" @click="selectedSheets = []">全不选</button></div><label v-for="(sheet, i) in sheets" :key="i"><input v-model="selectedSheets" type="checkbox" :value="i" /><span>{{ sheet.name }}（{{ sheet.items.length }} 条）<span class="sheet-sample">前 5 个单词：{{ sheet.items.slice(0, 5).map(item => item.word).join('、') || '无' }}</span></span></label></fieldset>
        <div v-if="skipped.length" role="status"><p class="fine">以下工作表已跳过：</p><ul><li v-for="(sheet, i) in skipped" :key="i">{{ sheet.name }}：{{ sheet.reason }}</li></ul></div>
        <p class="fine">必填 word、meaning；可选 example、example_translation。</p>
        <button class="secondary" :disabled="busy || (source === 'json' ? !importText.trim() : !selectedSheets.length)" @click="previewImport">生成表格预览</button>
        <template v-if="preview">
          <h2>检查预览 · {{ preview.items.length }} 条</h2>
          <fieldset v-for="(group, gi) in preview.duplicates" :key="gi" class="duplicate-group"><legend>重复组 {{ gi + 1 }}：{{ group.word }}</legend><p class="fine">必须选择保留一条，或全排除。相同释义也需确认。</p><label><input v-model="groupChoices[gi]" type="radio" :name="`group-${gi}`" value="none" :disabled="busy" @change="chooseGroup(gi)" />全排除</label><label v-for="i in group.indices" :key="i"><input v-model="groupChoices[gi]" type="radio" :name="`group-${gi}`" :value="String(i)" :disabled="busy" @change="chooseGroup(gi)" />保留第 {{ i + 1 }} 行：{{ preview.items[i]?.meaning }} · {{ preview.items[i]?.example }} · {{ preview.items[i]?.example_translation }}</label></fieldset>
          <div class="table-scroll"><table><caption>勾选排除；重复行请在对应重复组选择保留项</caption><thead><tr><th>排除</th><th>行</th><th>英文</th><th>释义</th><th>例句</th><th>译文</th></tr></thead><tbody><tr v-for="(word, i) in preview.items" :key="i" :class="{ excluded: !included[i] }"><td><input type="checkbox" :aria-label="`排除第 ${i + 1} 行`" :checked="!included[i]" :disabled="busy || preview.duplicates.some(g => g.indices.includes(i))" @change="included[i] = !($event.target as HTMLInputElement).checked" /></td><td>{{ i + 1 }}</td><td>{{ word.word }}</td><td>{{ word.meaning }}</td><td>{{ word.example }}</td><td>{{ word.example_translation }}</td></tr></tbody></table></div>
          <p v-if="!canImport" role="status">请完成每个重复组的选择。</p><button class="primary" :disabled="busy || !canImport || !selectedItems.length" @click="importDeck">确认导入 {{ selectedItems.length }} 条</button>
        </template>
      </section>
      <section v-else-if="page === 'manage' && detail" class="panel">
        <button class="text-button" @click="page = 'decks'">← 返回词库</button><h1>{{ detail.name }}</h1>
        <form class="content-tools" @submit.prevent="renameDeck"><label>词库名称<input v-model="rename" required maxlength="100" :disabled="busy" /></label><button class="secondary" :disabled="busy || !rename.trim()">保存名称</button></form>
        <div class="content-tools"><button class="secondary" :disabled="busy" @click="exportDeck">导出 JSON</button><button class="text-button danger" :disabled="busy" @click="deleteDeck">删除词库</button></div>
        <p class="fine">修改或删除词条、重置进度会结束包含该词条的活动批次。</p>
        <form v-if="wordDraft" class="example-editor" @submit.prevent="saveWord"><h2>编辑 {{ wordDraft.word }}</h2><label>释义<textarea v-model="wordDraft.meaning" required maxlength="2000" :disabled="busy"></textarea></label><label>英文例句<textarea v-model="wordDraft.example" maxlength="2000" :disabled="busy"></textarea></label><label>中文译文<textarea v-model="wordDraft.example_translation" maxlength="2000" :disabled="busy"></textarea></label><div class="content-tools"><button class="primary" :disabled="busy || !wordDraft.meaning.trim()">保存词条</button><button type="button" class="text-button" :disabled="busy" @click="wordDraft = null">取消</button></div></form>
        <section v-if="history" class="history-panel" aria-label="学习历史"><h2>{{ historyWord }} · 真实学习记录</h2><p>当前阶段：{{ history.stage }} · 下次复习：{{ dateTime(history.due_at) }}</p><ol class="stage-track"><li v-for="(interval, i) in history.intervals" :key="i" :class="{ current: i === history.stage }">阶段 {{ i }}<br>{{ intervalLabel(interval) }}</li></ol><p class="fine">阶段只表示复习间隔，不代表记忆概率；以下为真实答题时间线。</p><ol class="attempt-timeline"><li v-for="(attempt, i) in history.attempts" :key="i" :class="attempt.correct ? 'correct' : 'incorrect'"><strong>{{ attempt.correct ? '答对' : '答错' }}</strong> · {{ dateTime(attempt.created_at) }} · 第 {{ attempt.round_number }} 轮 · 输入：{{ attempt.answer || '（未输入）' }}</li></ol><p v-if="!history.attempts.length">暂无答题历史。</p><button class="text-button" @click="history = null">收起历史</button></section>
        <div v-if="detail.words.length" class="table-scroll"><table><caption>共 {{ detail.words.length }} 个词条</caption><thead><tr><th>英文</th><th>释义</th><th>例句 / 译文</th><th>学习进度</th><th>操作</th></tr></thead><tbody><tr v-for="word in detail.words" :key="word.id"><td>{{ word.word }}</td><td>{{ word.meaning }}</td><td>{{ word.example }}<br>{{ word.example_translation }}</td><td>阶段 {{ word.stage }}<br>答对 {{ word.correct }} / {{ word.attempts }}<br>下次：{{ dateTime(word.due_at) }}<br>最近：{{ dateTime(word.last_answer_at) }}</td><td><div class="word-tools"><button class="text-button" :disabled="busy" @click="wordDraft = { ...word }">编辑</button><button class="text-button" :disabled="busy" @click="showHistory(word)">历史趋势</button><button class="text-button danger" :disabled="busy" @click="changeWord(word, true)">重置进度</button><button class="text-button danger" :disabled="busy" @click="changeWord(word, false)">删除</button></div></td></tr></tbody></table></div><p v-else>词库为空。仍可改名、导出或删除。</p>
      </section>
      <template v-else-if="page === 'family' && isParent"><div class="section-heading"><div><p class="eyebrow">陪伴成长</p><h1>家庭管理</h1></div></div><section class="panel"><h2>创建孩子账号</h2><form class="child-form" @submit.prevent="createChild"><label>用户名<input v-model="childName" autocomplete="off" required maxlength="150" placeholder="孩子的用户名" /></label><label>初始密码<input v-model="childPassword" autocomplete="new-password" type="password" required placeholder="设置初始密码" /></label><button class="primary" :disabled="busy">创建账号</button></form></section><section class="panel family-list"><h2>孩子与账号</h2><p class="fine">重置密码后，请将新密码告知孩子。停用不会删除学习记录。</p><div v-for="child in users.filter(u => u.id !== user?.id)" :key="child.id" class="child-row"><div class="child-info"><strong>{{ child.username }}</strong><span :class="['badge', { inactive: (child.is_active === false || child.is_active === 0) }]">{{ (child.is_active === false || child.is_active === 0) ? '已停用' : '正常' }}</span></div><form class="reset-form" @submit.prevent="updateChild(child, true)"><label class="sr-only" :for="`password-${child.id}`">{{ child.username }} 的新密码</label><input :id="`password-${child.id}`" v-model="resetPasswords[child.id]" type="password" autocomplete="new-password" required placeholder="输入新密码" /><button class="secondary" :disabled="busy">重置密码</button></form><button class="text-button" :class="{ danger: child.is_active !== false && child.is_active !== 0 }" :disabled="busy" @click="updateChild(child, false)">{{ (child.is_active === false || child.is_active === 0) ? '启用账号' : '停用账号' }}</button></div><p v-if="!users.some(u => u.id !== user?.id)" class="muted">还没有孩子账号，在上方创建第一个。</p></section></template>
      <footer>一点一点，把单词记在心间。</footer>
    </main>
  </template>
</template>
