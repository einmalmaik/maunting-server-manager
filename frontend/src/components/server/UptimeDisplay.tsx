import { useEffect, useState } from 'react'
import { Server } from '@/types'
import { formatDurationSeconds } from '@/utils/timeFormat'

interface UptimeDisplayProps {
  server: Server
  label: string
}

export function UptimeDisplay({ server, label }: UptimeDisplayProps) {
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
      uptimeStr = formatDurationSeconds(Math.max(0, Math.floor((now - started) / 1000)))
    }
  } else {
    uptimeStr = formatDurationSeconds(server.uptime_seconds)
  }

  return (
    <span>
      {label}: {uptimeStr}
    </span>
  )
}
