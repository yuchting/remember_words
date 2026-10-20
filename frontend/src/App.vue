<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, onUnmounted } from 'vue'
import { deckStatistics, errorMessage, parseImport, reviewComplete } from './utils'
import StatisticsPanel from './StatisticsPanel.vue'

type User = { id: number; username: string; role: string; is_active?: boolean | number }
type Deck = { id: number; name: string; total: number; due: number; learned?: number; started_at?: string }
type Progress = { id: number; round_number: number; total: number; answered: number; correct: number; remaining: number; deck_id?: number | null; deck_name?: string; kind?: string }
type Item = Progress & { meaning: string; practice_pending?: boolean }
const activeStudies = ref<Progress[]>([])
const reviewDeckIds = ref<number[]>([])
const reviewCounts = ref<Record<number, number>>({})
const selectedReviewCount = computed(() => reviewDeckIds.value.reduce((sum, id) => sum + (reviewCounts.value[id] ?? 0), 0))
function deckProgress(id: number) { return activeStudies.value.find(progress => progress.deck_id === id) }
type Content = { practice_pending?: boolean; practice?: boolean; spelling_correct?: boolean; word: string; meaning: string; example?: string; example_translation?: string; audio?: string; phonetic?: string; correct?: boolean; complete?: boolean; word_id?: number; definitions?: string[] }
type Summary = { attempts: number; correct: number }
type ImportWord = { word: string; meaning: string; example?: string; example_translation?: string }
type Preview = { items: ImportWord[]; duplicates: { word: string; indices: number[] }[] }
type Sheet = { name: string; items: ImportWord[] }
type SheetResult = { sheets: Sheet[]; skipped: { name: string; reason: string }[] }
type ManagedWord = ImportWord & { id: number; stage: number; due_at: number | null; attempts: number; correct: number; last_answer_at: number | null }
type DeckDetail = { id: number; name: string; words: ManagedWord[] }
type History = { stage: number; due_at: number | null; intervals: number[]; attempts: { correct: boolean; created_at: number; answer: string; round_number: number }[] }
type SpeechProvider = 'azure' | 'google' | 'off'
type SpeechSettings = { primary: SpeechProvider; fallback: SpeechProvider; browser_fallback: boolean; azure: { region: string; voice: string; key_set: boolean }; google: { voice: string; key_set: boolean } }
type SpeechCache = { files: number; bytes: number; max_bytes: number }
const speechCache = ref<SpeechCache | null>(null)
const cacheSize = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(2)} MiB`
const speechSettings = ref<SpeechSettings | null>(null)
const speechPublic = ref({ enabled: false, browser_fallback: false })
const speechKeys = ref({ azure: '', google: '' })
const speechClear = ref({ azure: false, google: false })
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
const statistics = computed(() => deckStatistics(detail.value?.words ?? []))
async function openStatistics(id: number) {
  await openDeck(id)
  if (!error.value) page.value = 'statistics'
}
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
let lastInputAt = 0
let fluent = false
let inputCount = 0
function resetFluency() {
  lastInputAt = performance.now(); fluent = !item.value?.practice_pending; inputCount = 0
}
function trackInput(insertion: boolean) {
  const now = performance.now()
  fluent = fluent && insertion && now - lastInputAt < 1000
  lastInputAt = now; inputCount++
}
function beforeInput(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.selectionStart !== input.value.length || input.selectionEnd !== input.value.length) fluent = false
}
function nativeInput(event: Event) {
  const e = event as InputEvent
  const input = event.target as HTMLInputElement
  trackInput(e.inputType === 'insertText' && !e.isComposing && !!e.data && [...e.data].length === 1 &&
    input.selectionStart === input.value.length && input.selectionEnd === input.value.length)
}
const continueButton = ref<HTMLButtonElement | null>(null)
const cooldownSeconds = ref(0)
let cooldownUntil = 0
let cooldownTimer: ReturnType<typeof setInterval> | undefined
function clearCooldown() {
  clearInterval(cooldownTimer); cooldownTimer = undefined
  cooldownUntil = 0; cooldownSeconds.value = 0
}
function startCooldown(seconds = 2) {
  clearCooldown()
  cooldownUntil = performance.now() + seconds * 1000
  cooldownSeconds.value = seconds
  cooldownTimer = setInterval(() => {
    cooldownSeconds.value = Math.max(0, Math.ceil((cooldownUntil - performance.now()) / 1000))
    if (!cooldownSeconds.value) {
      clearCooldown()
      if (!editingExample.value && (document.activeElement === document.body || document.activeElement?.matches(':disabled'))) void nextTick(() => continueButton.value?.focus())
    }
  }, 100)
}
async function continueStudy() {
  if (busy.value || !answered.value || performance.now() < cooldownUntil) return
  await run(loadNext)
}
function studyEnter(event: KeyboardEvent) {
  if (event.key !== 'Enter' || !session.value || !item.value || report.value || summary.value || editingExample.value) return
  if (event.target !== document.body && event.target !== answerInput.value && event.target !== continueButton.value) return
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (event.repeat || busy.value) return
  if (answered.value) void continueStudy()
  else if (event.target === answerInput.value) void submit()
}
watch(session, clearCooldown, { flush: 'sync' })
const editingExample = ref(false)
const exampleDraft = ref('')
const translationDraft = ref('')
const answerInput = ref<HTMLInputElement | null>(null)
const keyboard = ref(false)
const isParent = computed(() => user.value?.role === 'parent' || user.value?.role === 'admin')
const totalWords = computed(() => decks.value.reduce((sum, deck) => sum + deck.total, 0))
const rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
const exportPassword = ref('')
const restorePassword = ref('')
const restoreFile = ref<File | null>(null)
const restoreInput = ref<HTMLInputElement | null>(null)
const restoreUnderstood = ref(false)
const restoreConfirmation = ref('')
const validRestoreFile = (file: File | null) => !!file && /\.(sqlite3|db)$/i.test(file.name) && file.size > 0 && file.size <= 50 * 1024 * 1024
const canRestore = computed(() => isParent.value && validRestoreFile(restoreFile.value) && restoreUnderstood.value && restoreConfirmation.value === '确认覆盖整个数据库' && !!restorePassword.value)
watch(restoreFile, () => { restoreUnderstood.value = false; restoreConfirmation.value = ''; restorePassword.value = '' }, { flush: 'sync' })
watch([restoreUnderstood, restoreConfirmation], () => { restorePassword.value = '' }, { flush: 'sync' })
function clearDatabaseForm() {
  exportPassword.value = ''; restoreFile.value = null; restorePassword.value = ''; restoreUnderstood.value = false; restoreConfirmation.value = ''
  if (restoreInput.value) restoreInput.value.value = ''
}
watch(user, () => { clearDatabaseForm() }, { flush: 'sync' })
function selectRestoreFile(event: Event) {
  if (busy.value) return
  const input = event.target as HTMLInputElement
  restoreFile.value = null
  const file = input.files?.[0] ?? null
  if (file && !validRestoreFile(file)) { error.value = '请选择非空 .sqlite3 或 .db 文件，最大 50 MiB'; input.value = ''; return }
  error.value = ''; restoreFile.value = file
}
async function exportDatabase() {
  if (!isParent.value || !exportPassword.value) return
  await run(async () => {
    try {
      const blob = await api<Blob>('/admin/database/export', 'POST', { password: exportPassword.value }, 'download')
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = 'words-backup.sqlite3'
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
      notice.value = '数据库备份已下载。含密码哈希等敏感数据，请妥善保管，不要分享。'
    } finally { exportPassword.value = '' }
  })
}
async function restoreDatabase() {
  if (busy.value || !canRestore.value) return
  if (!confirm('确认覆盖整个数据库？当前所有账户、词库、学习记录将被备份替换，不会合并；所有人退出登录，必须使用备份内家长密码重新登录。此操作不可撤销，仅主机自动备份可回退。')) return
  await run(async () => {
    try {
      const result = await api<{ backup: string }>('/admin/database/restore', 'POST', restoreFile.value!, 'raw', {
        'X-Admin-Password': encodeURIComponent(restorePassword.value), 'X-Restore-Confirmation': 'OVERWRITE_ALL_DATA',
      })
      clearAudio(); user.value = null; session.value = null; csrf = ''; username.value = ''; password.value = ''; childPassword.value = ''; resetPasswords.value = {}
      clearDatabaseForm(); decks.value = []; users.value = []; due.value = 0; item.value = null; content.value = null; summary.value = null; detail.value = null; wordDraft.value = null; history.value = null
      clearSheets(); importText.value = ''; driveUrl.value = ''; targetUser.value = ''; page.value = 'decks'
      notice.value = `整个数据库已覆盖恢复，所有人已退出登录。主机自动备份文件：${result.backup}。请使用备份内家长账号和密码登录。`
    } catch (cause) {
      if (cause instanceof TypeError) throw new Error('恢复请求连接中断，结果未知；请先重新登录核实数据库状态，不要直接重试覆盖恢复。')
      throw cause
    } finally { restorePassword.value = '' }
  })
}

const personalExportPassword = ref('')
const personalPassword = ref('')
const personalFile = ref<File | null>(null)
const personalInput = ref<HTMLInputElement | null>(null)
const personalUnderstood = ref(false)
const personalConfirmation = ref('')
const canRestorePersonal = computed(() => !!user.value && !!personalFile.value && personalUnderstood.value && personalConfirmation.value === '确认覆盖我的学习数据' && !!personalPassword.value)
function clearPersonalForm() {
  personalExportPassword.value = ''; personalFile.value = null; personalPassword.value = ''; personalUnderstood.value = false; personalConfirmation.value = ''
  if (personalInput.value) personalInput.value.value = ''
}
watch(personalFile, () => { personalUnderstood.value = false; personalConfirmation.value = ''; personalPassword.value = '' }, { flush: 'sync' })
watch([personalUnderstood, personalConfirmation], () => { personalPassword.value = '' }, { flush: 'sync' })
watch(user, clearPersonalForm, { flush: 'sync' })
function selectPersonalFile(event: Event) {
  if (busy.value) return
  const input = event.target as HTMLInputElement
  personalFile.value = null
  const file = input.files?.[0] ?? null
  if (file && (!/\.json$/i.test(file.name) || !file.size || file.size > 2 * 1024 * 1024)) {
    error.value = '请选择非空个人备份 JSON 文件，最大 2 MiB；整库 SQLite 备份不能用于个人恢复'; input.value = ''; return
  }
  error.value = ''; personalFile.value = file
}
async function exportPersonalBackup() {
  if (!user.value || !personalExportPassword.value) return
  await run(async () => {
    try {
      const blob = await api<Blob>('/account/backup/export', 'POST', { password: personalExportPassword.value }, 'download')
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = 'words-personal-backup.json'
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
      notice.value = '个人备份已下载，含全部词库、历史和进度，不含用户名或密码。请妥善保管。'
    } finally { personalExportPassword.value = '' }
  })
}
async function restorePersonalBackup() {
  if (busy.value || !canRestorePersonal.value) return
  if (!confirm('确认覆盖我的学习数据？当前用户全部词库、学习历史和进度将被备份替换，不会合并，当前学习将结束。不影响其他用户，不修改任何账户名或密码。网页无法撤销，仅主机 backups/ 下自动备份可回退。')) return
  await run(async () => {
    try {
      const file = personalFile.value!
      const currentPassword = personalPassword.value
      const text = await file.text()
      if (!canRestorePersonal.value || personalFile.value !== file || personalPassword.value !== currentPassword) throw new Error('恢复确认或文件已变化，请重新确认')
      let backup: unknown
      try { backup = JSON.parse(text) } catch { throw new Error('个人备份不是有效 JSON 文件') }
      if (!backup || typeof backup !== 'object' || Array.isArray(backup) || !('format' in backup) || backup.format !== 'remember_words_user' || !('version' in backup) || backup.version !== 1) throw new Error('个人备份必须为 remember_words_user version 1；不能使用整库备份')
      const payload = { password: personalPassword.value, confirmation: 'OVERWRITE_MY_DATA', backup }
      if (new TextEncoder().encode(JSON.stringify(payload)).byteLength > 2 * 1024 * 1024) throw new Error('个人恢复请求不能超过 2 MiB（包括密码和确认信息）')
      const result = await api<{ backup: string }>('/account/backup/restore', 'POST', payload)
      clearAudio(); session.value = null; item.value = null; detail.value = null; content.value = null; summary.value = null; wordDraft.value = null; history.value = null
      password.value = ''; childPassword.value = ''; resetPasswords.value = {}; clearDatabaseForm(); clearPersonalForm()
      clearSheets(); importText.value = ''; driveUrl.value = ''; targetUser.value = ''; decks.value = []; due.value = 0; page.value = 'decks'
      notice.value = `个人学习数据已覆盖恢复。账户名和密码未改变。主机自动备份文件：${result.backup}（backups/）。`
      // 恢复已成功；刷新失败不能诱导用户再次覆盖。
      try { await refresh() } catch { error.value = '个人数据已恢复，但词库刷新失败，请重新加载页面；不要重复恢复。' }
    } catch (cause) {
      if (cause instanceof TypeError) throw new Error('个人恢复请求连接中断，结果未知；请重新加载页面核实，不要直接重试覆盖恢复。')
      throw cause
    } finally { personalPassword.value = '' }
  })
}

async function api<T>(path: string, method = 'GET', body?: unknown, mode?: 'raw' | 'download', headers: Record<string, string> = {}): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}api${path}`, {
    method, credentials: 'same-origin',
    headers: { ...(body === undefined ? {} : { 'Content-Type': mode === 'raw' ? 'application/octet-stream' : 'application/json' }), ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrf }), ...headers },
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
  finally {
    busy.value = false
    await nextTick()
    if (session.value && item.value && !answered.value && !content.value && !report.value && !summary.value) answerInput.value?.focus()
  }
}
async function refresh() {
  const [list, review, active] = await Promise.all([api<Deck[]>('/decks'), api<{ count: number; decks: { deck_id: number; count: number; active_session: Progress | null }[] }>('/reviews/due'), api<{ sessions: Progress[] }>('/study/active')])
  decks.value = list; due.value = review.count; activeStudies.value = active.sessions
  reviewCounts.value = Object.fromEntries(review.decks.map(deck => [deck.deck_id, deck.count]))
  reviewDeckIds.value = reviewDeckIds.value.filter(id => list.some(deck => deck.id === id))
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
async function openSpeech() {
  await run(async () => {
    speechSettings.value = await api<SpeechSettings>('/admin/speech')
    speechKeys.value = { azure: '', google: '' }; speechClear.value = { azure: false, google: false }
    speechCache.value = await api<SpeechCache>('/admin/speech/cache')
    page.value = 'speech'
  })
}
async function refreshSpeechCache() {
  await run(async () => { speechCache.value = await api<SpeechCache>('/admin/speech/cache') })
}
async function clearSpeechCache() {
  if (!confirm('确定清空全部云端语音缓存？学习记录和设置不变；下次朗读将重新调用云端服务，可能产生费用。')) return
  await run(async () => {
    speechCache.value = await api<SpeechCache>('/admin/speech/cache', 'DELETE')
    notice.value = '云端语音缓存已清空'
  })
}
async function saveSpeech() {
  if (!speechSettings.value) return
  await run(async () => {
    const settings = speechSettings.value!
    await api('/admin/speech', 'PUT', {
      primary: settings.primary, fallback: settings.fallback, browser_fallback: settings.browser_fallback,
      azure: { region: settings.azure.region, voice: settings.azure.voice, key: speechKeys.value.azure, clear_key: speechClear.value.azure },
      google: { voice: settings.google.voice, key: speechKeys.value.google, clear_key: speechClear.value.google },
    })
    speechKeys.value = { azure: '', google: '' }; speechClear.value = { azure: false, google: false }
    speechSettings.value = await api<SpeechSettings>('/admin/speech')
    speechPublic.value = await api<{ enabled: boolean; browser_fallback: boolean }>('/speech/settings')
    notice.value = '朗读设置已保存；试听使用已保存配置。'
  })
}
async function testSpeechProvider(provider: 'azure' | 'google') {
  if (!speechSettings.value) return
  clearAudio()
  notice.value = ''
  const version = audioVersion
  const settings = speechSettings.value[provider]
  await run(async () => {
    const blob = await api<Blob>('/admin/speech/test-provider', 'POST', {
      provider,
      config: { voice: settings.voice, ...('region' in settings ? { region: settings.region } : {}), key: speechKeys.value[provider], clear_key: speechClear.value[provider] },
    }, 'download')
    if (version !== audioVersion) return
    playAudio(URL.createObjectURL(blob), () => { notice.value = ''; error.value = '测试音频播放失败，请检查浏览器播放权限' }, true, () => {
      notice.value = `${provider === 'azure' ? 'Azure' : 'Google'} 测试音频已开始播放，请确认是否听到声音。当前输入未保存。`
    })
  })
}
async function testSpeech() {
  clearAudio()
  const version = audioVersion
  await run(async () => {
    const blob = await api<Blob>('/admin/speech/test', 'POST', {}, 'download')
    if (version === audioVersion) playAudio(URL.createObjectURL(blob), () => { error.value = '云端试听播放失败，请检查浏览器播放权限' }, true)
  })
}
watch(user, () => {
  clearAudio(); speechSettings.value = null
  speechKeys.value = { azure: '', google: '' }; speechClear.value = { azure: false, google: false }
}, { flush: 'sync' })
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
type RoundReport = { round_number: number; correct: number; wrong: number; unanswered: number; words: { word_id: number; word: string; meaning: string; correct: boolean | number | null; evaluation: string; stage: number; due_at: number | null; next_round_probability: number }[] }
const report = ref<RoundReport | null>(null)
const reportWords = computed(() => [...(report.value?.words ?? [])].sort((a, b) => {
  const rank = (word: RoundReport['words'][number]) => word.correct === null ? 1 : word.correct ? 2 : 0
  return rank(a) - rank(b)
}))
const pendingReport = ref<number | null>(null)
async function roundAction(action: 'interrupt' | 'finish') {
  if (!item.value || !confirm(action === 'interrupt' ? '放弃本轮全部答题和提示记录，不计成绩，下次从本轮开头重来？' : '提前结算本轮？只计已答成绩，错误和未答词进入下一轮。')) return
  await run(async () => {
    const number = item.value!.round_number
    await api(`/study/${session.value}/round-action`, 'POST', { action, round_number: number })
    if (action === 'finish') {
      report.value = await api<RoundReport>(`/study/${session.value}/rounds/${number}`)
    } else {
      await refresh(); session.value = null; item.value = null; content.value = null
    }
    pendingReport.value = null
  })
}
async function loadNext() {
  clearCooldown()
  clearAudio()
  if (pendingReport.value !== null) {
    report.value = await api<RoundReport>(`/study/${session.value}/rounds/${pendingReport.value}`)
    pendingReport.value = null
    return
  }
  report.value = null
  const data = await api<{ complete: boolean; item?: Item; summary?: Summary }>(`/study/${session.value}/next`)
  item.value = data.item ?? null; summary.value = data.complete ? (data.summary ?? { attempts: 0, correct: 0 }) : null
  content.value = null; answer.value = ''; answered.value = false; editingExample.value = false
  if (data.complete) await refresh()
  await nextTick(); resetFluency()
}
async function showPreviousReport(progress: Progress) {
  if (progress.round_number < 2) return
  await run(async () => {
    session.value = progress.id
    report.value = await api<RoundReport>(`/study/${session.value}/rounds/${progress.round_number - 1}`)
    pendingReport.value = null
  })
}
async function resumeStudy(progress: Progress) {
  await run(async () => {
    report.value = null; pendingReport.value = null
    session.value = progress.id; summary.value = null; item.value = null; content.value = null
    await loadNext()
  })
}
async function start(deck?: Deck) {
  await run(async () => {
    const data = await api<{ id: number }>(deck ? `/decks/${deck.id}/study` : '/reviews/study', 'POST', deck ? undefined : { deck_ids: reviewDeckIds.value })
    await refresh()
    report.value = null; pendingReport.value = null
    session.value = data.id; summary.value = null; item.value = null; content.value = null; await loadNext()
  })
}
async function reveal() {
  fluent = false
  await run(async () => {
    content.value = await api<Content>(`/study/${session.value}/items/${item.value?.id}/reveal`, 'POST')
    void speak(content.value.word, content.value.audio, content.value.word_id)
  })
}
async function submit(skip = false) {
  if (answered.value || !item.value || (!skip && !answer.value.trim())) return
  const fast = fluent && inputCount > 0 && performance.now() - lastInputAt < 1000 && !skip && !content.value
  await run(async () => {
    const result = await api<Content & { round_finished?: boolean; round_number?: number }>(`/study/${session.value}/items/${item.value?.id}/answer`, 'POST', { answer: answer.value.trim(), skip, practice: !!item.value?.practice_pending })
    content.value = result
    if (result.round_finished) pendingReport.value = result.round_number!
    answered.value = true
    startCooldown(result.correct && !result.practice ? (fast ? 1 : 2) : result.practice && result.spelling_correct ? 2 : 3)
    void speak(result.word, result.audio, result.word_id)
  })
  await nextTick(); continueButton.value?.focus()
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
  trackInput(value !== 'Backspace' && start === end && end === answer.value.length)
  answer.value = answer.value.slice(0, from) + (value === 'Backspace' ? '' : value) + answer.value.slice(end)
  nextTick(() => { input?.focus(); const position = from + (value === 'Backspace' ? 0 : value.length); input?.setSelectionRange(position, position) })
}
let activeAudio: HTMLAudioElement | null = null
let audioTimer: ReturnType<typeof setTimeout> | undefined
let audioUrl: string | null = null
let audioVersion = 0
function clearAudio() {
  audioVersion++
  clearTimeout(audioTimer); audioTimer = undefined
  if (activeAudio) {
    activeAudio.onplaying = null; activeAudio.onerror = null; activeAudio.onended = null
    activeAudio.pause(); activeAudio.removeAttribute('src'); activeAudio.load(); activeAudio = null
  }
  if (audioUrl) { URL.revokeObjectURL(audioUrl); audioUrl = null }
  if ('speechSynthesis' in window) speechSynthesis.cancel()
}
function playAudio(url: string, fallback: () => void, owned = false, playing?: () => void) {
  const player = new Audio(url)
  activeAudio = player
  if (owned) audioUrl = url
  const failed = () => {
    if (activeAudio !== player) return
    clearAudio(); fallback()
  }
  player.onplaying = () => { clearTimeout(audioTimer); audioTimer = undefined; playing?.() }
  player.onerror = failed
  player.onended = () => { if (activeAudio === player) clearAudio() }
  audioTimer = setTimeout(failed, 8000)
  player.play().catch(failed)
}
function browserSpeak(text: string) {
  if (!('speechSynthesis' in window)) { error.value = '浏览器不支持语音播放'; return }
  speechSynthesis.cancel()
  const voices = speechSynthesis.getVoices?.() ?? []
  const voice = voices.find(candidate => /^en(?:-|_)/i.test(candidate.lang))
  if (voices.length && !voice) { error.value = '设备没有可用英语语音，请安装英语语音包或配置云端朗读'; return }
  const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'en-US'; utterance.rate = 0.85
  if (voice) utterance.voice = voice
  utterance.onerror = () => { error.value = '语音播放失败，请检查浏览器英语语音设置' }
  speechSynthesis.speak(utterance)
}
function legacySpeak(text: string, audio?: string) {
  if (audio && /^(https?:\/\/|\/)/i.test(audio) && !audio.startsWith('//')) playAudio(audio, () => browserSpeak(text))
  else browserSpeak(text)
}
async function speak(text: string, audio?: string, wordId?: number, kind: 'word' | 'example' = 'word') {
  clearAudio()
  const version = audioVersion
  if (wordId) {
    try {
      const settings = await api<{ enabled: boolean; browser_fallback: boolean }>('/speech/settings')
      if (version !== audioVersion) return
      speechPublic.value = settings
    } catch (cause) {
      if (version === audioVersion) error.value = cause instanceof Error ? cause.message : '无法读取朗读设置'
      return
    }
    if (version !== audioVersion) return
  }
  if (!speechPublic.value.enabled) { legacySpeak(text, audio); return }
  const failed = (cause: unknown) => {
    if (speechPublic.value.browser_fallback) browserSpeak(text)
    else error.value = cause instanceof Error ? cause.message : '云端朗读失败；未启用设备语音回退'
  }
  if (!wordId) { failed(new Error('缺少词条编号，无法请求云端朗读')); return }
  try {
    const blob = await api<Blob>('/speech', 'POST', { word_id: wordId, kind }, 'download')
    if (version !== audioVersion) return
    playAudio(URL.createObjectURL(blob), () => failed(new Error('云端音频播放失败，请检查浏览器播放权限')), true)
  } catch (cause) {
    if (version !== audioVersion) return
    if (cause instanceof Error && cause.message === '云端朗读未启用') { legacySpeak(text, audio); return }
    failed(cause)
  }
}
watch(page, clearAudio, { flush: 'sync' })
async function leaveStudy() {
  clearCooldown()
  clearAudio()
  await run(async () => { await refresh(); session.value = null; item.value = null; content.value = null; summary.value = null; report.value = null; pendingReport.value = null })
}
onUnmounted(() => {
  window.removeEventListener('keydown', studyEnter)
  clearCooldown(); clearAudio()
})
onMounted(async () => {
  window.addEventListener('keydown', studyEnter)
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
      <div class="section-heading"><button class="text-button" :disabled="busy" @click="leaveStudy">← 返回词库</button><span class="muted">{{ summary ? '本轮完成' : `第 ${item?.round_number ?? 1} 轮 · 已完成 ${item?.answered ?? 0}/${item?.total ?? '…'} · 剩余 ${item?.remaining ?? '…'} 词` }}</span></div>
      <section v-if="report" class="panel"><h1>第 {{ report.round_number }} 轮报告</h1><p>正确 {{ report.correct }} · 错误 {{ report.wrong }} · 未答 {{ report.unanswered }}（不计分）</p><p class="fine">下一轮入选率按规则计算，不是记忆概率。阶段不是精确记忆分数。</p><div class="table-scroll"><table><caption class="sr-only">本轮单词结果，错词优先</caption><thead><tr><th scope="col">单词</th><th scope="col">释义</th><th scope="col">结果</th><th scope="col">下一轮入选率</th><th scope="col">记忆阶段</th></tr></thead><tbody><tr v-for="word in reportWords" :key="word.word_id" :class="{ 'report-wrong': word.correct !== null && !word.correct }"><th scope="row" lang="en">{{ word.word }}</th><td>{{ word.meaning }}</td><td>{{ word.evaluation }}</td><td>{{ word.next_round_probability }}%</td><td>{{ word.stage < 0 ? '未学' : word.stage }}</td></tr></tbody></table></div><button :disabled="busy" @click="run(loadNext)">继续</button></section>
      <section v-else-if="summary" class="panel result-panel"><span class="result-mark">✓</span><p class="eyebrow">每次回忆，都算进步</p><h1>这一轮，完成了！</h1><div class="result-stats"><div><strong>{{ summary.attempts }}</strong><span>回答次数</span></div><div><strong>{{ summary.correct }}</strong><span>正确次数</span></div><div><strong>{{ summary.attempts ? Math.round(summary.correct / summary.attempts * 100) : 0 }}%</strong><span>正确率</span></div></div><p class="muted">让记忆休息一下，到期后再来巩固。</p><button class="primary" :disabled="busy" @click="leaveStudy">回到词库</button></section>
      <section v-else-if="item" class="panel study-panel">
        <div class="study-actions"><button :disabled="busy || pendingReport !== null" @click="roundAction('interrupt')">中断本轮，不计成绩</button><button :disabled="busy || pendingReport !== null" @click="roundAction('finish')">提前完结，结算已答</button></div>
        <p class="eyebrow">看中文 · 回忆英文</p><h1 class="meaning">{{ item.meaning }}</h1>
        <form @submit.prevent="submit()"><label class="sr-only" for="answer">输入英文单词</label><input id="answer" ref="answerInput" v-model="answer" @beforeinput="beforeInput" @input="nativeInput" @paste="fluent = false" @cut="fluent = false" @drop="fluent = false" class="answer-input" lang="en" autocomplete="off" autocapitalize="none" :spellcheck="false" :disabled="busy || answered" placeholder="在这里输入英文" /><div class="study-actions"><button type="button" class="secondary" :disabled="busy || answered || !!content" @click="reveal">揭示答案</button><button type="button" class="secondary" :disabled="busy || answered" @click="submit(true)">不会，记一下</button><button class="primary" :disabled="busy || answered || !answer.trim()">提交答案</button></div></form>
        <div v-if="content" class="answer-card" aria-live="polite"><p v-if="answered" :class="['answer-status', content.correct ? 'correct' : 'incorrect']">{{ content.practice && content.spelling_correct ? '巩固拼写正确；首次成绩不变。' : content.correct ? '回答正确，记得不错！' : '还没记牢，继续后重输同一个词。' }}</p><div class="word-line"><h2>{{ content.word }}</h2><span class="muted">{{ content.phonetic }}</span><button class="audio-button" aria-label="播放单词发音" @click="speak(content.word, content.audio, content.word_id)">朗读单词</button></div><p>{{ content.meaning }}</p><ul v-if="content.definitions?.length" lang="en"><li v-for="definition in content.definitions.slice(0, 5)" :key="definition">{{ definition }}</li></ul><template v-if="content.example"><div class="example-line"><p lang="en">{{ content.example }}</p><button class="text-button" @click="speak(content.example!, undefined, content.word_id, 'example')">朗读例句</button></div><p class="muted">{{ content.example_translation }}</p></template><p v-else class="muted">暂无例句，可查词补全或手工添加。</p><div v-if="content.word_id" class="content-tools"><button class="secondary" :disabled="busy || editingExample" @click="enrich">查词补全</button><button class="text-button" :disabled="busy" @click="editExample">编辑例句与译文</button></div><form v-if="editingExample" class="example-editor" @submit.prevent="saveExample"><label>完整英文例句<textarea v-model="exampleDraft" lang="en" required maxlength="2000" rows="3" placeholder="填写完整英文句子"></textarea></label><label>完整中文译文<textarea v-model="translationDraft" required maxlength="2000" rows="3" placeholder="填写对应中文译文"></textarea></label><div class="content-tools"><button class="primary" :disabled="busy">保存例句与译文</button><button type="button" class="text-button" :disabled="busy" @click="editingExample = false">取消</button></div></form><p v-if="!answered" class="fine">看过答案也没关系，输入一遍加深印象，或选择“不会”。</p><button v-if="answered" ref="continueButton" class="primary wide" :disabled="busy || cooldownSeconds > 0" @click="continueStudy">{{ busy ? '正在加载…' : cooldownSeconds > 0 ? `继续（${cooldownSeconds}秒）` : '继续 →' }}</button></div>
        <div v-if="!answered" class="keyboard-wrap"><button class="text-button keyboard-toggle" :aria-expanded="keyboard" @click="keyboard = !keyboard">{{ keyboard ? '收起软键盘' : '展开软键盘' }}</button><div v-if="keyboard" class="keyboard" aria-label="英文软键盘"><div v-for="row in rows" :key="row" class="keyboard-row"><button v-for="letter in row" :key="letter" :disabled="busy" @mousedown.prevent @click="key(letter)">{{ letter }}</button></div><div class="keyboard-row"><button :disabled="busy" @mousedown.prevent @click="key('-')">-</button><button :disabled="busy" @mousedown.prevent @click="key('\'')">'</button><button class="space-key" :disabled="busy" @mousedown.prevent @click="key(' ')">空格</button><button class="delete-key" aria-label="删除一个字符" :disabled="busy" @mousedown.prevent @click="key('Backspace')">⌫</button></div></div></div>
      </section>
      <section v-else class="panel"><p>学习内容暂未载入。</p><button class="primary" :disabled="busy" @click="run(loadNext)">重新加载</button></section>
    </main>
    <main v-else class="dashboard">
      <nav class="tabs" aria-label="主要导航"><button :class="{ active: page === 'decks' }" @click="page = 'decks'">我的词库</button><button :class="{ active: page === 'overall-statistics' }" @click="page = 'overall-statistics'">总体统计</button><button :class="{ active: page === 'import' }" @click="page = 'import'">导入词库</button><button :disabled="busy" :class="{ active: page === 'backup' }" @click="page = 'backup'">个人备份</button><button v-if="isParent" :class="{ active: page === 'family' }" @click="page = 'family'">家庭管理</button><button v-if="isParent" :disabled="busy" :class="{ active: page === 'speech' }" @click="openSpeech">云端朗读</button></nav>
      <template v-if="page === 'decks'">
        <section v-for="progress in activeStudies" :key="progress.id" class="panel" aria-label="已保存的学习进度"><h2>{{ progress.deck_name || '旧版跨词库复习' }} · 第 {{ progress.round_number }} 轮</h2><p>本轮已完成 {{ progress.answered }}/{{ progress.total }} 词，剩余 {{ progress.remaining }} 词。{{ progress.round_number > 1 ? '本轮仅练上一轮未独立答对的词。' : '本轮覆盖本次学习的全部单词。' }}</p><p class="fine">每次提交自动保存；提示、不会和答错的词会进入下一轮，直到独立答对。</p><button :disabled="busy" @click="resumeStudy(progress)">继续学习</button><button v-if="progress.round_number > 1" :disabled="busy" @click="showPreviousReport(progress)">查看上一轮报告</button></section>
        <section class="welcome"><div><p class="eyebrow">把今天的一点，变成明天的更多</p><h1>你好，{{ user.username }}。</h1><p class="muted">不必一次记住所有，只要每天向前一点。</p><p class="fine">各词库独立保存轮次与进度；切换词库不会丢失已答记录。</p></div><div class="today-date">{{ new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }) }}</div></section>
        <section class="review-banner"><div><span class="eyebrow">今日复习</span><h2>{{ due ? `${due} 个单词，等你再见一面` : '今天的复习，已安排妥当' }}</h2><p>{{ due ? '趁记忆还在，给它一点巩固。' : '暂时没有到期单词，挑一个词库开始学习吧。' }}</p></div><button :disabled="busy || !selectedReviewCount" @click="start()">复习所选词库（{{ selectedReviewCount }} 词）</button></section>
        <fieldset class="panel"><legend>选择到期复习词库</legend><p class="fine">仅纳入已独立过关且到期的单词；未学和尚未过关词不进入复习。已有批次从保存位置继续。</p><button class="text-button" :disabled="busy" @click="reviewDeckIds = decks.filter(deck => (reviewCounts[deck.id] ?? 0) > 0).map(deck => deck.id)">全选可复习词库</button><button class="text-button" :disabled="busy" @click="reviewDeckIds = []">取消全选</button><label v-for="deck in decks" :key="deck.id" class="review-choice"><input v-model="reviewDeckIds" type="checkbox" :value="deck.id" :disabled="busy || !(reviewCounts[deck.id] > 0)" />{{ deck.name }}（{{ reviewCounts[deck.id] ?? 0 }} 词可复习）</label></fieldset>
        <div class="section-heading"><h2>我的词库 <span class="count">{{ decks.length }}</span></h2><span class="muted">共 {{ totalWords }} 个单词</span></div>
        <div v-if="decks.length" class="deck-grid"><article v-for="(deck, index) in decks" :key="deck.id" class="panel deck-card"><span class="deck-icon" :class="`tone-${index % 3}`">{{ deck.name?.slice(0, 1) || '词' }}</span><h3>{{ deck.name || '未命名词库' }}</h3><p class="muted">{{ deck.total }} 个单词 <span class="dot">·</span> 已过关 {{ deck.learned ?? 0 }} 个 <span class="dot">·</span> {{ deck.due }} 个待复习</p><button class="secondary" :disabled="busy" @click="openStatistics(deck.id)">统计信息</button><button class="secondary" :disabled="busy" @click="openDeck(deck.id)">管理词库</button><p v-if="deckProgress(deck.id)" class="fine">第 {{ deckProgress(deck.id)!.round_number }} 轮 · 已答 {{ deckProgress(deck.id)!.answered }}/{{ deckProgress(deck.id)!.total }}</p><div class="deck-footer"><span class="fine">{{ deck.started_at ? '已开始学习' : '等待初次相遇' }}</span><button class="text-button" :disabled="busy || !deck.total" @click="start(deck)">{{ deck.started_at ? '继续学习' : '开始学习' }} →</button></div></article></div>
        <section v-else class="panel empty"><h3>第一本词库，从这里开始</h3><p class="muted">导入一份 JSON 单词表，开启你的记忆旅程。</p><button class="primary" @click="page = 'import'">导入词库</button></section>
      </template>
      <StatisticsPanel v-else-if="page === 'overall-statistics'" :load="api" :decks="decks" />
      <section v-else-if="page === 'speech' && isParent && speechSettings" class="panel speech-panel" aria-labelledby="speech-heading">
        <h1 id="speech-heading">云端朗读设置</h1>
        <p>家庭共用。云端启用后，单词和例句由服务器按主服务、备用服务顺序合成；设备语音回退默认关闭。关闭主服务则使用原有词典音频／设备语音。</p>
        <p class="fine">API key 只提交给本站后端，不会回显。配置教程见项目 README 的“云端朗读”。云端合成可能收费，并向服务商发送朗读文本。</p>
        <form @submit.prevent="saveSpeech">
          <fieldset :disabled="busy" class="speech-routing">
            <legend>服务顺序</legend>
            <label>主服务<select v-model="speechSettings.primary"><option value="off">关闭云端朗读</option><option value="azure">Azure</option><option value="google">Google</option></select></label>
            <label>备用服务<select v-model="speechSettings.fallback"><option value="off">不使用备用</option><option value="azure">Azure</option><option value="google">Google</option></select></label>
            <label class="speech-check"><input v-model="speechSettings.browser_fallback" type="checkbox" />云端失败后允许设备英语语音回退（默认关闭）</label>
          </fieldset>
          <div class="speech-providers">
          <fieldset :disabled="busy" class="speech-provider">
            <legend>Azure AI Speech</legend>
            <a class="text-button" href="https://portal.azure.com/#view/Microsoft_Azure_Billing/ModernBillingMenuBlade/~/Overview" target="_blank" rel="noopener noreferrer">查看额度／账单（新窗口）</a>
            <p class="fine">登录 Azure 后选择语音资源对应的订阅；免费层、用量和费用以官方控制台为准。</p>
            <label>Region<input v-model="speechSettings.azure.region" placeholder="eastus（须与资源区域一致）" autocomplete="off" /></label>
            <label>Voice<input v-model="speechSettings.azure.voice" placeholder="en-US-JennyNeural" autocomplete="off" /></label>
            <div class="speech-key-row"><label>API Key<span class="speech-key-input"><input v-model="speechKeys.azure" type="password" autocomplete="new-password" :placeholder="speechSettings.azure.key_set ? '*****' : '请输入 API Key'" :disabled="speechClear.azure" aria-describedby="azure-key-help" /><span class="speech-key-status" role="status">{{ speechSettings.azure.key_set ? '已设置' : '未设置' }}</span></span></label><button type="button" class="secondary" :disabled="busy || speechClear.azure" @click="testSpeechProvider('azure')">测试一下</button></div>
            <p id="azure-key-help" class="fine">{{ speechSettings.azure.key_set ? '***** 表示服务器已保存密钥；留空保留，输入新密钥则替换。' : '填写密钥后保存；也可先点击“测试一下”。' }}</p>
            <label class="speech-check"><input v-model="speechClear.azure" type="checkbox" />保存时清除 Azure key</label>
          </fieldset>
          <fieldset :disabled="busy" class="speech-provider">
            <legend>Google Cloud Text-to-Speech</legend>
            <a class="text-button" href="https://console.cloud.google.com/billing" target="_blank" rel="noopener noreferrer">查看额度／账单（新窗口）</a>
            <p class="fine">登录 Google Cloud 后选择对应项目的结算账号，查看费用、赠金及用量；免费额度不等于账户余额。</p>
            <label>Voice<input v-model="speechSettings.google.voice" placeholder="en-US-Neural2-F" autocomplete="off" /></label>
            <div class="speech-key-row"><label>API Key<span class="speech-key-input"><input v-model="speechKeys.google" type="password" autocomplete="new-password" :placeholder="speechSettings.google.key_set ? '*****' : '请输入 API Key'" :disabled="speechClear.google" aria-describedby="google-key-help" /><span class="speech-key-status" role="status">{{ speechSettings.google.key_set ? '已设置' : '未设置' }}</span></span></label><button type="button" class="secondary" :disabled="busy || speechClear.google" @click="testSpeechProvider('google')">测试一下</button></div>
            <p id="google-key-help" class="fine">{{ speechSettings.google.key_set ? '***** 表示服务器已保存密钥；留空保留，输入新密钥则替换。' : '填写密钥后保存；也可先点击“测试一下”。' }}</p>
            <label class="speech-check"><input v-model="speechClear.google" type="checkbox" />保存时清除 Google key</label>
          </fieldset>
          </div>
          <p class="fine">“测试一下”使用该服务当前填写的音色、区域与密钥（密钥留空则用已保存密钥），不保存设置、不走备用或缓存，可能产生少量费用。播放开始不代表扬声器一定有声音，请实际试听。</p>
          <div class="content-tools"><button class="primary" :disabled="busy">保存设置</button><button type="button" class="secondary" :disabled="busy" @click="testSpeech">试听已保存配置</button></div>
        </form>
        <h2>语音缓存</h2>
        <p v-if="speechCache" role="status">{{ speechCache.files }} 个文件，已用 {{ cacheSize(speechCache.bytes) }} / {{ cacheSize(speechCache.max_bytes) }}</p>
        <p class="fine">按文本、服务和音色去重，单词／例句共用缓存。上限 100 MiB，超限删除最旧文件；缓存不属于备份内容。</p>
        <div class="content-tools"><button class="secondary" :disabled="busy" @click="refreshSpeechCache">刷新容量</button><button class="text-button danger" :disabled="busy" @click="clearSpeechCache">清空缓存</button></div>
      </section>
      <section v-else-if="page === 'backup'" class="panel database-panel" aria-labelledby="personal-heading">
        <h1 id="personal-heading">个人备份</h1><p>所有用户均可备份自己的全部词库、学习历史和进度；不包含用户名或密码。个人 JSON 备份与整库 SQLite 备份不兼容。</p>
        <form @submit.prevent="exportPersonalBackup"><fieldset :disabled="busy" class="database-fields"><legend>下载个人备份</legend><label>再次输入当前密码<input v-model="personalExportPassword" type="password" autocomplete="current-password" required /></label><button class="secondary" :disabled="busy || !personalExportPassword">下载个人 JSON 备份</button></fieldset></form>
        <div class="database-warning" role="alert"><strong>危险：覆盖我的全部学习数据，不是合并！</strong><p>当前用户的全部词库、学习历史和进度会被备份替换，当前学习将结束。不影响其他用户，不修改任何账户名或密码。</p><p>网页无法撤销；仅主机 backups/ 下的恢复前自动备份可回退。个人恢复不会退出登录。</p></div>
        <form @submit.prevent="restorePersonalBackup"><fieldset :disabled="busy" class="database-fields"><legend>覆盖恢复个人学习数据</legend>
          <label>选择个人 JSON 备份（请求总大小最大 2 MiB）<input ref="personalInput" type="file" accept=".json,application/json" required @change="selectPersonalFile" /></label>
          <p v-if="personalFile" class="fine">已选择：{{ personalFile.name }}</p>
          <label class="risk-check"><input v-model="personalUnderstood" type="checkbox" />我已理解覆盖不可撤销，以及仅主机自动备份可回退</label>
          <label>输入精确文字：确认覆盖我的学习数据<input v-model="personalConfirmation" autocomplete="off" :spellcheck="false" required /></label>
          <label>当前密码<input v-model="personalPassword" type="password" autocomplete="current-password" required /></label>
          <button class="danger restore-button" :disabled="busy || !canRestorePersonal">{{ busy ? '操作进行中…' : '覆盖我的全部学习数据' }}</button>
        </fieldset></form>
      </section>
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
      <section v-else-if="page === 'statistics' && detail" class="panel">
        <button class="text-button" @click="page = 'decks'">← 返回词库</button><h1>{{ detail.name }} · 统计信息</h1>
        <button :disabled="busy" @click="run(reloadDetail)">刷新统计</button>
        <h2>已过关 {{ statistics.percentage }}%</h2>
        <progress :value="statistics.passed" :max="statistics.total || 1" aria-label="词库已过关比例">{{ statistics.percentage }}%</progress>
        <div class="result-stats"><div><strong>{{ statistics.total }}</strong><span>全部单词</span></div><div><strong>{{ statistics.passed }}</strong><span>已过关</span></div><div><strong>{{ statistics.unlearned }}</strong><span>尚未过关</span></div><div><strong>{{ statistics.due }}</strong><span>到期待复习</span></div></div>
        <p class="fine">已过关指阶段 0–5：至少独立答对并完成轮次结算；尚未过关指未学阶段 -1，包含尚未独立过关的词。到期词属于已过关词，需要继续巩固。百分比不是记忆概率，也不代表永久掌握。当前轮未结算的答题不改变此统计。</p>
        <h2>记忆阶段分布</h2><ul><li v-for="stage in statistics.stages" :key="stage.stage">{{ stage.stage < 0 ? '未学 / 尚未过关' : `阶段 ${stage.stage}` }}：{{ stage.count }} 词</li></ul>
        <p v-if="!statistics.total">词库为空，暂无学习统计。</p>
        <h2>尚未过关的单词</h2><ul><li v-for="word in detail.words.filter(word => word.stage < 0)" :key="word.id">{{ word.word }} · {{ word.meaning }}</li></ul>
        <p v-if="statistics.total && !statistics.unlearned">全部词都已过关，请按期复习。</p>
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
      <template v-else-if="page === 'family' && isParent"><div class="section-heading"><div><p class="eyebrow">陪伴成长</p><h1>家庭管理</h1></div></div><section class="panel"><h2>创建孩子账号</h2><form class="child-form" @submit.prevent="createChild"><label>用户名<input v-model="childName" :disabled="busy" autocomplete="off" required maxlength="150" placeholder="孩子的用户名" /></label><label>初始密码<input v-model="childPassword" :disabled="busy" autocomplete="new-password" type="password" required placeholder="设置初始密码" /></label><button class="primary" :disabled="busy">创建账号</button></form></section><section class="panel family-list"><h2>孩子与账号</h2><p class="fine">重置密码后，请将新密码告知孩子。停用不会删除学习记录。</p><div v-for="child in users.filter(u => u.id !== user?.id)" :key="child.id" class="child-row"><div class="child-info"><strong>{{ child.username }}</strong><span :class="['badge', { inactive: (child.is_active === false || child.is_active === 0) }]">{{ (child.is_active === false || child.is_active === 0) ? '已停用' : '正常' }}</span></div><form class="reset-form" @submit.prevent="updateChild(child, true)"><label class="sr-only" :for="`password-${child.id}`">{{ child.username }} 的新密码</label><input :id="`password-${child.id}`" v-model="resetPasswords[child.id]" :disabled="busy" type="password" autocomplete="new-password" required placeholder="输入新密码" /><button class="secondary" :disabled="busy">重置密码</button></form><button class="text-button" :class="{ danger: child.is_active !== false && child.is_active !== 0 }" :disabled="busy" @click="updateChild(child, false)">{{ (child.is_active === false || child.is_active === 0) ? '启用账号' : '停用账号' }}</button></div><p v-if="!users.some(u => u.id !== user?.id)" class="muted">还没有孩子账号，在上方创建第一个。</p></section>
        <section class="panel database-panel" aria-labelledby="database-heading">
          <h2 id="database-heading">整个数据库备份与覆盖恢复</h2>
          <p>备份包含所有账户、词库、学习记录及密码哈希等敏感数据。请妥善保管，不要分享。</p>
          <form @submit.prevent="exportDatabase"><fieldset :disabled="busy" class="database-fields"><legend>下载整个数据库</legend><label>再次输入当前管理员密码<input v-model="exportPassword" type="password" autocomplete="current-password" required /></label><button class="secondary" :disabled="busy || !exportPassword">下载 SQLite 备份</button></fieldset></form>
          <div class="database-warning" role="alert"><strong>危险：覆盖整个数据库，不是合并！</strong><p>当前所有账户、词库、学习记录都会被备份内容替换，不会合并。所有人都会退出登录，恢复后必须使用备份内家长账号和密码登录。</p><p>网页无法撤销此操作；仅主机上的恢复前自动备份可用于回退。</p></div>
          <form @submit.prevent="restoreDatabase"><fieldset :disabled="busy" class="database-fields"><legend>覆盖恢复整个数据库</legend>
            <label>选择 SQLite 备份（.sqlite3 / .db，最大 50 MiB）<input ref="restoreInput" type="file" accept=".sqlite3,.db" required @change="selectRestoreFile" /></label>
            <p v-if="restoreFile" class="fine">已选择：{{ restoreFile.name }}</p>
            <label class="risk-check"><input v-model="restoreUnderstood" type="checkbox" />我已理解覆盖不可撤销的风险，以及仅主机自动备份可回退</label>
            <label>输入精确文字：确认覆盖整个数据库<input v-model="restoreConfirmation" autocomplete="off" :spellcheck="false" required /></label>
            <label>当前管理员密码（不是备份内密码）<input v-model="restorePassword" type="password" autocomplete="current-password" required /></label>
            <button class="danger restore-button" :disabled="busy || !canRestore">{{ busy ? '操作进行中…' : '覆盖整个数据库并退出所有登录' }}</button>
          </fieldset></form>
        </section>
      </template>
      <footer>一点一点，把单词记在心间。</footer>
    </main>
  </template>
</template>
