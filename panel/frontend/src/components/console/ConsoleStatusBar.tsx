import { Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ConnectionStatus } from '@/hooks/useConsoleStream'
import type { ConsoleSource } from '@/lib/types'
import { useUiLanguage } from '@/lib/ui-language'

interface ConsoleStatusBarProps {
  status: ConnectionStatus
  errorMessage: string | null
  lineCount: number
  filteredCount: number
  source: ConsoleSource
  onReconnect: () => void
}

function StatusIndicator({ status }: { status: ConnectionStatus }) {
  if (status === 'connected') {
    return (
      <span className="relative flex h-2 w-2">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
      </span>
    )
  }
  if (status === 'connecting' || status === 'reconnecting') {
    return <Loader2 className="h-2.5 w-2.5 animate-spin text-amber-400" />
  }
  return (
    <span
      className={cn(
        'h-2 w-2 rounded-full',
        status === 'error' ? 'bg-red-500' : 'bg-muted-foreground/50',
      )}
    />
  )
}

export default function ConsoleStatusBar({
  status,
  errorMessage,
  lineCount,
  filteredCount,
  source,
  onReconnect,
}: ConsoleStatusBarProps) {
  const { copy } = useUiLanguage()
  const sourceLabel: Record<ConsoleSource, string> = {
    log: copy.console.sourceLog,
  }
  const statusLabel: Record<ConnectionStatus, string> = {
    idle: copy.console.idle,
    connecting: copy.console.connecting,
    connected: copy.console.connected,
    reconnecting: copy.console.reconnecting,
    error: copy.console.error,
    closed: copy.console.disconnected,
  }
  const lineLabel =
    filteredCount < lineCount
      ? copy.console.filteredLines(filteredCount, lineCount)
      : copy.console.lines(lineCount)

  return (
    <div
      className={cn(
        'flex items-center gap-3 px-3 py-1.5',
        'border-t border-border/50 bg-[var(--el-1)]',
        'text-xs text-muted-foreground select-none',
      )}
    >
      <div className="flex items-center gap-1.5">
        <StatusIndicator status={status} />
        <span
          className={cn(
            status === 'connected' && 'text-emerald-400',
            status === 'error' && 'text-red-400',
            (status === 'connecting' || status === 'reconnecting') && 'text-amber-400',
          )}
        >
          {statusLabel[status]}
        </span>
      </div>

      <span className="text-border">·</span>
      <span>{sourceLabel[source]}</span>
      <span className="text-border">·</span>
      <span>{lineLabel}</span>

      {errorMessage && (
        <>
          <span className="text-border">·</span>
          <span className="text-red-400 truncate max-w-xs" title={errorMessage}>
            {errorMessage}
          </span>
        </>
      )}

      <div className="flex-1" />

      {(status === 'error' || status === 'closed') && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onReconnect}
          className="h-5 gap-1 text-xs text-muted-foreground hover:text-foreground px-2"
        >
          <RefreshCw className="h-2.5 w-2.5" />
          {copy.console.reconnect}
        </Button>
      )}
    </div>
  )
}
