export function errorMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(errorMessage).join('；')
  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>
    if (typeof value.msg === 'string') return value.msg
    if ('detail' in value) return errorMessage(value.detail)
    return JSON.stringify(detail)
  }
  return '请求失败，请稍后重试'
}

export function parseImport(text: string): Record<string, unknown>[] {
  const value: unknown = JSON.parse(text)
  if (!Array.isArray(value) || !value.length) throw new Error('JSON 必须是非空单词数组')
  if (!value.every(item => item && typeof item === 'object' && !Array.isArray(item)
    && typeof item.word === 'string' && item.word.trim()
    && typeof item.meaning === 'string' && item.meaning.trim())) {
    throw new Error('每项必须包含非空字符串 word（英文）和 meaning（中文）')
  }
  return value as Record<string, unknown>[]
}
