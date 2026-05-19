import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'
import type { ConsoleLine, LogLevel } from '@/lib/types'
import { useUiLanguage } from '@/lib/ui-language'

const LEVEL_CLASS: Record<LogLevel, string> = {
  ERROR: 'text-red-400',
  WARN: 'text-amber-400',
  INFO: 'text-sky-400',
  DEBUG: 'text-purple-400/80',
  SCRIPT: 'text-emerald-400/80',
  ADMIN: 'text-orange-400',
  PLAIN: 'text-foreground/70',
}

interface ConsoleTerminalProps {
  lines: ConsoleLine[]
  terminalRef: React.RefObject<HTMLDivElement>
  onScrollChange: (scrolledUp: boolean) => void
  emptyMessage?: string
}

export default function ConsoleTerminal({
  lines,
  terminalRef,
  onScrollChange,
  emptyMessage,
}: ConsoleTerminalProps) {
  const { copy } = useUiLanguage()
  const isUserScrolledRef = useRef(false)

  useEffect(() => {
    if (isUserScrolledRef.current) return
    const element = terminalRef.current
    if (element) {
      element.scrollTo({ top: element.scrollHeight, behavior: 'auto' })
    }
  }, [lines, terminalRef])

  const handleScroll = () => {
    const element = terminalRef.current
    if (!element) return
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40
    const scrolledUp = !atBottom
    if (scrolledUp !== isUserScrolledRef.current) {
      isUserScrolledRef.current = scrolledUp
      onScrollChange(scrolledUp)
    }
  }

  return (
    <div
      ref={terminalRef}
      onScroll={handleScroll}
      className={cn(
        'flex-1 min-h-0 overflow-y-auto',
        'bg-[var(--el-0)] font-mono text-xs leading-relaxed',
        'p-3 select-text',
      )}
    >
      {lines.length === 0 ? (
        <span className="text-muted-foreground/50 italic">
          {emptyMessage ?? copy.console.waiting}
        </span>
      ) : (
        lines.map((line) => (
          <div
            key={line.id}
            className={cn('whitespace-pre-wrap break-all py-px', LEVEL_CLASS[line.level])}
          >
            {line.text}
          </div>
        ))
      )}
    </div>
  )
}
