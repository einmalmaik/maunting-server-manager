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

/**
 * „Gerade eben", „vor 12 Min.", „vor 3 Std." — und darüber das volle Datum.
 *
 * Die Staffelung stand dreimal im Code: bei den gekoppelten Geräten, bei den
 * Stories und im Kontakt-Tab, jedes Mal mit eigenem Wortlaut. Sie liegt jetzt
 * hier, damit dieselbe Zeitspanne überall gleich heißt.
 */
export function formatRelativeTime(
  wann: string | number | Date,
  t: (schluessel: string, werte?: Record<string, unknown>) => string,
): string {
  const datum = wann instanceof Date ? wann : new Date(wann)
  if (Number.isNaN(datum.getTime())) return '-'
  const sekunden = Math.max(0, Math.floor((Date.now() - datum.getTime()) / 1000))
  if (sekunden < 60) return t('common.timeAgo.justNow')
  const minuten = Math.floor(sekunden / 60)
  if (minuten < 60) return t('common.timeAgo.minutes', { count: minuten })
  const stunden = Math.floor(minuten / 60)
  if (stunden < 24) return t('common.timeAgo.hours', { count: stunden })
  return datum.toLocaleString()
}

export function getAvailableTimezones(): string[] {
  try {
    const intlWithSupported = Intl as typeof Intl & {
      supportedValuesOf?: (key: string) => string[]
    }
    if (typeof intlWithSupported.supportedValuesOf === 'function') {
      return intlWithSupported.supportedValuesOf('timeZone')
    }
  } catch {
    // Fallback falls Browser-API nicht verfügbar
  }
  return [
    'UTC',
    'Europe/Berlin',
    'Europe/London',
    'Europe/Paris',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
    'Asia/Tokyo',
    'Asia/Singapore',
    'Australia/Sydney',
  ]
}

