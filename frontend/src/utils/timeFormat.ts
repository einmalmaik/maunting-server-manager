export type PanelTimeFormat = '24h' | '12h'

export function formatPanelTime(value: string, format: PanelTimeFormat): string {
  if (format === '24h') return value
  if (!value || !value.includes(':')) return value
  const [rawHour, minute] = value.split(':')
  const hour = Number(rawHour)
  if (!Number.isFinite(hour) || minute === undefined) return value
  const period = hour >= 12 ? 'PM' : 'AM'
  const displayHour = hour % 12 || 12
  return `${displayHour}:${minute} ${period}`
}

export function formatPanelDateTime(value: string | null | undefined, format: PanelTimeFormat, locale: string): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: format === '12h',
  }).format(date)
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '-'
  const total = Math.floor(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}
