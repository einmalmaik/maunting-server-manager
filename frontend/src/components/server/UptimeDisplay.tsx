import { useEffect, useState } from 'react'
import { Server } from '@/types'
import { formatDurationSeconds } from '@/utils/timeFormat'

interface UptimeDisplayProps {
  server: Server
  label: string
  compact?: boolean
}

export function formatCompactUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '-'
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const days = Math.floor(totalSeconds / 86_400)
  const hours = Math.floor((totalSeconds % 86_400) / 3_600)
  const minutes = Math.floor((totalSeconds % 3_600) / 60)
  if (days > 0) return `${days}d ${hours}h ${minutes}m`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export function UptimeDisplay({ server, label, compact = false }: UptimeDisplayProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (server.status !== 'running') return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [server.status])

  if (server.status !== 'running') {
    return <span>-</span>
  }

  let uptimeStr = '-'
  if (server.started_at) {
    const started = new Date(server.started_at).getTime()
    if (!Number.isNaN(started)) {
      const elapsedSeconds = Math.max(0, Math.floor((now - started) / 1000))
      uptimeStr = compact ? formatCompactUptime(elapsedSeconds) : formatDurationSeconds(elapsedSeconds)
    }
  } else {
    uptimeStr = compact ? formatCompactUptime(server.uptime_seconds) : formatDurationSeconds(server.uptime_seconds)
  }

  return (
    <span>
      {label ? `${label}: ` : ''}{uptimeStr}
    </span>
  )
}
