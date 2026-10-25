<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Counts = { total: number; passed: number; due: number; stages: Record<string, number> }
type Round = { attempts: number; correct: number; accuracy: number | null }
type Statistics = {
  totals: Counts
  decks: (Counts & { id: number; name: string })[]
  mistakes: { word_id: number; word: string; meaning: string; deck_id: number; deck_name: string; attempts: number; wrong: number; error_rate: number; last_wrong_at: number | null }[]
  first_round: { new: Round; review: Round }
  delayed_recall?: Round
  daily: { date: string; attempts: number; correct: number; new_passed: null }[]
  new_passed_available: false
  new_passed_note: string
  upcoming_due: { date: string; count: number }[]
}

const props = defineProps<{
  load: (path: string) => Promise<unknown>
  decks: { id: number; name: string }[]
}>()
const range = ref<'7' | '30' | 'all'>('7')
const deckId = ref('')
const data = ref<Statistics | null>(null)
const loading = ref(false)
const error = ref('')
let requestId = 0
const rangeLabel = computed(() => range.value === 'all' ? '全部历史' : `最近 ${range.value} 天`)
const stages = computed(() => Object.entries(data.value?.totals.stages ?? {}).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })))
const mistakes = computed(() => (data.value?.mistakes ?? []).filter(row => row.attempts > 0 && row.wrong > 0)
  .slice().sort((a, b) => b.wrong - a.wrong || b.error_rate - a.error_rate || a.word_id - b.word_id).slice(0, 20))
const percent = (value: number | null) => value === null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(1)}%`
const ratio = (part: number, total: number) => total > 0 ? Math.min(1, Math.max(0, part / total)) : 0
const utcTime = (epoch: number | null) => {
  if (epoch === null) return '—'
  const date = new Date(epoch * 1000)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN')
}

async function refresh() {
  const current = ++requestId
  loading.value = true
  error.value = ''
  data.value = null
  const query = new URLSearchParams({ range: range.value })
  if (deckId.value) query.set('deck_id', deckId.value)
  try {
    const result = await props.load(`/statistics?${query}`) as Statistics
    if (!result || !result.totals || !result.totals.stages || !result.first_round?.new || !result.first_round?.review
      || !Array.isArray(result.decks) || !Array.isArray(result.mistakes)
      || !Array.isArray(result.daily) || !Array.isArray(result.upcoming_due)) {
      throw new Error('统计响应格式无效')
    }
    if (current === requestId) data.value = result
  } catch (cause) {
    if (current === requestId) error.value = cause instanceof Error ? cause.message : '统计加载失败，请重试'
  } finally {
    if (current === requestId) loading.value = false
  }
}

watch([range, deckId], refresh)
watch(() => props.decks, decks => {
  if (deckId.value && !decks.some(deck => String(deck.id) === deckId.value)) deckId.value = ''
}, { deep: true })
onMounted(refresh)
onBeforeUnmount(() => { requestId++ })
</script>

<template>
  <section class="panel statistics-panel" :aria-busy="loading" aria-label="学习统计">
    <h2>学习统计</h2>
    <div class="statistics-filters">
      <label>词库
        <select v-model="deckId">
          <option value="">所有词库</option>
          <option v-for="deck in decks" :key="deck.id" :value="String(deck.id)">{{ deck.name }}</option>
        </select>
      </label>
      <label>历史时间范围（UTC）
        <select v-model="range">
          <option value="7">最近 7 天</option>
          <option value="30">最近 30 天</option>
          <option value="all">全部历史</option>
        </select>
      </label>
      <button class="secondary" type="button" :disabled="loading" @click="refresh">刷新</button>
    </div>
    <p v-if="loading" role="status">正在加载统计…</p>
    <p v-if="error" class="statistics-error" role="alert">加载失败：{{ error }}</p>
    <template v-if="data">
      <section>
        <h3>当前状态快照</h3>
        <p class="muted">当前词条状态，不受历史时间范围影响；到期按当前时刻判断。</p>
        <p>词库 {{ data.decks.length }} · 总词数 {{ data.totals.total }} · 尚未过关 {{ data.totals.total - data.totals.passed }} · 已过关 {{ data.totals.passed }}（{{ percent(ratio(data.totals.passed, data.totals.total)) }}）· 当前到期 {{ data.totals.due }}</p>
        <h4>各词库过关比例</h4>
        <p v-if="!data.decks.length">暂无词库数据</p>
        <div v-for="deck in data.decks" :key="deck.id" class="statistics-bar">
          <span>{{ deck.name }}</span>
          <progress :value="ratio(deck.passed, deck.total)" :max="1" :aria-label="`${deck.name}过关比例`" />
          <span>{{ deck.passed }} / {{ deck.total }}（{{ percent(ratio(deck.passed, deck.total)) }}）</span>
        </div>
        <h4>整体阶段分布</h4>
        <p class="muted">占当前总词数；阶段不是精确记忆概率。</p>
        <p v-if="!stages.length">暂无阶段数据</p>
        <div v-for="[stage, count] in stages" :key="stage" class="statistics-bar">
          <span>{{ stage === '-1' || stage === 'unlearned' || stage === 'new' ? '未学 / 尚未过关' : `阶段 ${stage}` }}</span>
          <progress :value="ratio(count, data.totals.total)" :max="1" :aria-label="`阶段 ${stage}占总词数比例`" />
          <span>{{ count }}（{{ percent(ratio(count, data.totals.total)) }}）</span>
        </div>
        <h4>到期分布（UTC 日期）</h4>
        <p class="muted">当前调度快照，不是历史作答记录；日期及分组以服务端返回为准。</p>
        <p v-if="!data.upcoming_due.length">暂无到期数据</p>
        <div v-for="day in data.upcoming_due" :key="day.date" class="statistics-bar">
          <span>{{ day.date }}</span>
          <progress :value="ratio(day.count, data.totals.total)" :max="1" :aria-label="`${day.date} UTC 到期词数占总词数比例`" />
          <span>{{ day.count }} 词</span>
        </div>
      </section>
      <section>
        <h3>历史表现：{{ rangeLabel }}（UTC）</h3>
        <p class="muted">仅展示所选范围内作答；每日按 UTC 日期统计。首轮正确率只统计各批次第 1 轮独立答对的比例，不代表永久掌握；最近出错时间按设备本地时间显示。</p>
        <div class="table-scroll" role="region" aria-label="首轮表现" tabindex="0">
          <table>
            <caption>首轮表现</caption>
            <thead><tr><th scope="col">类型</th><th scope="col">作答</th><th scope="col">答对</th><th scope="col">正确率</th></tr></thead>
            <tbody>
              <tr><th scope="row">词库学习</th><td>{{ data.first_round.new.attempts }}</td><td>{{ data.first_round.new.correct }}</td><td>{{ percent(data.first_round.new.accuracy) }}</td></tr>
              <tr><th scope="row">复习</th><td>{{ data.first_round.review.attempts }}</td><td>{{ data.first_round.review.correct }}</td><td>{{ percent(data.first_round.review.accuracy) }}</td></tr>
            </tbody>
          </table>
        </div>
        <template v-if="data.delayed_recall">
          <h4>间隔回忆表现</h4>
          <p>检验 {{ data.delayed_recall.attempts }} 次 · 独立答对 {{ data.delayed_recall.correct }} 次 · 正确率 {{ percent(data.delayed_recall.accuracy) }}</p>
          <p class="muted">距上次接触至少 10 分钟的检验单独统计；错误计为未独立答对；揭示答案后的提交与即时重输不计入。10 分钟是统计分界，不是长期掌握标准；旧记录缺少间隔信息时不推算。</p>
        </template>
        <h4>每日作答 / 答对</h4>
        <p>{{ data.new_passed_note || '历史新增过关数不可用，不能从当前状态推算。' }}</p>
        <p v-if="!data.daily.some(day => day.attempts > 0)">此范围暂无作答记录</p>
        <div v-else class="table-scroll" role="region" aria-label="每日作答统计" tabindex="0">
          <table>
            <thead><tr><th scope="col">日期（UTC）</th><th scope="col">作答次数</th><th scope="col">答对次数</th></tr></thead>
            <tbody><tr v-for="day in data.daily.filter(row => row.attempts > 0)" :key="day.date"><th scope="row">{{ day.date }}</th><td>{{ day.attempts }}</td><td>{{ day.correct }}</td></tr></tbody>
          </table>
        </div>
        <h4>高频错词 Top 20</h4>
        <p class="muted">按错误次数降序；同次数按错误率降序。错误率 = 错误次数 / 作答次数。</p>
        <p v-if="!mistakes.length">此范围暂无错词</p>
        <div v-else class="table-scroll" role="region" aria-label="高频错词" tabindex="0">
          <table>
            <thead><tr><th scope="col">单词 / 释义</th><th scope="col">词库</th><th scope="col">错误 / 作答</th><th scope="col">错误率</th><th scope="col">最近错误（本地时间）</th></tr></thead>
            <tbody><tr v-for="word in mistakes" :key="word.word_id"><th scope="row">{{ word.word }}<small>{{ word.meaning }}</small></th><td>{{ word.deck_name }}</td><td>{{ word.wrong }} / {{ word.attempts }}</td><td>{{ percent(word.error_rate) }}</td><td>{{ utcTime(word.last_wrong_at) }}</td></tr></tbody>
          </table>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.statistics-panel { margin-top: 24px; min-width: 0; }
.statistics-filters { display: flex; flex-wrap: wrap; align-items: end; gap: 16px; margin: 20px 0; }
.statistics-filters label { flex: 1; min-width: 160px; }
.statistics-filters button { padding: 12px 18px; }
.statistics-panel section { margin-top: 24px; }
.statistics-panel h4 { margin: 20px 0 10px; }
.statistics-panel p { margin: 8px 0; }
.statistics-error { color: #a43b35; overflow-wrap: anywhere; }
.statistics-bar { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(80px, 2fr) minmax(120px, 1fr); align-items: center; gap: 12px; margin: 12px 0; overflow-wrap: anywhere; }
.statistics-bar progress { width: 100%; height: 16px; accent-color: #315c45; }
.statistics-panel small { display: block; font-weight: normal; }
.statistics-panel .table-scroll { margin: 12px 0; }
@media (max-width: 680px) {
  .statistics-bar { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px 12px; }
  .statistics-bar progress { grid-row: 2; grid-column: 1 / -1; }
  .statistics-bar > span:last-child { text-align: right; }
}
</style>
