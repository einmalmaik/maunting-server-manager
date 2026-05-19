import { ArrowDown, FileDown, TerminalSquare, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { ConsoleSource } from '@/lib/types'
import { useUiLanguage } from '@/lib/ui-language'

interface ConsoleToolbarProps {
  source: ConsoleSource
  onSourceChange: (s: ConsoleSource) => void
  filter: string
  onFilterChange: (v: string) => void
  onClear: () => void
  onDownload: () => void
  onJumpToBottom: () => void
  isUserScrolled: boolean
}

export default function ConsoleToolbar({
  source,
  onSourceChange,
  filter,
  onFilterChange,
  onClear,
  onDownload,
  onJumpToBottom,
  isUserScrolled,
}: ConsoleToolbarProps) {
  const { copy } = useUiLanguage()
  const sourceOptions: { value: ConsoleSource; label: string }[] = [
    { value: 'log', label: copy.console.sourceLog },
  ]

  return (
    <div className="flex flex-wrap items-center gap-2 pt-3">
      <div className="flex items-center rounded-md border border-border/60 overflow-hidden">
        {sourceOptions.map((opt) => (
          <button
            type="button"
            key={opt.value}
            onClick={() => onSourceChange(opt.value)}
            aria-pressed={source === opt.value}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1 text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              source === opt.value
                ? 'bg-primary/10 text-primary border-r border-border/60 last:border-r-0'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50 border-r border-border/60 last:border-r-0',
            )}
          >
            <TerminalSquare className="h-3 w-3" />
            {opt.label}
          </button>
        ))}
      </div>

      <Input
        value={filter}
        onChange={(event) => onFilterChange(event.target.value)}
        placeholder={copy.console.filterPlaceholder}
        aria-label={copy.console.filterAria}
        className="h-7 w-44 font-mono text-xs bg-[var(--el-1)] border-border/60 placeholder:text-muted-foreground/50"
      />

      <div className="flex-1" />

      <Button
        variant="secondary"
        size="sm"
        onClick={onJumpToBottom}
        aria-hidden={!isUserScrolled || undefined}
        className={cn(
          'h-7 gap-1.5 text-xs transition-opacity duration-150',
          isUserScrolled ? 'opacity-100' : 'opacity-0 pointer-events-none',
        )}
        tabIndex={isUserScrolled ? 0 : -1}
      >
        <ArrowDown className="h-3 w-3" />
        {copy.console.jumpToBottom}
      </Button>

      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 text-muted-foreground hover:text-foreground"
        onClick={onClear}
        title={copy.console.clearBuffer}
      >
        <Trash2 className="h-3.5 w-3.5" />
        <span className="sr-only">{copy.console.clearBuffer}</span>
      </Button>

      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 text-muted-foreground hover:text-foreground"
        onClick={onDownload}
        title={copy.console.downloadLogs}
      >
        <FileDown className="h-3.5 w-3.5" />
        <span className="sr-only">{copy.console.downloadLogs}</span>
      </Button>
    </div>
  )
}
