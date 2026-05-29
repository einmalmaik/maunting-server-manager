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
