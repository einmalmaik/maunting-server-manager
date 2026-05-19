import { useCallback, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Terminal } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useConsoleStream } from '@/hooks/useConsoleStream'
import ConsoleTerminal from '@/components/console/ConsoleTerminal'
import ConsoleToolbar from '@/components/console/ConsoleToolbar'
import ConsoleStatusBar from '@/components/console/ConsoleStatusBar'
import { serversApi } from '@/lib/api'
import type { ServersData } from '@/lib/types'
import { useUiLanguage } from '@/lib/ui-language'

export default function ConsolePage() {
  const terminalRef = useRef<HTMLDivElement>(null)
  const [isUserScrolled, setIsUserScrolled] = useState(false)
  const { copy } = useUiLanguage()

  const { data: serversData } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const currentServerName = serversData?.current ?? null

  const {
    lines,
    filteredLines,
    lineCount,
    filter,
    setFilter,
    source,
    setSource,
    status,
    errorMessage,
    clear,
    connect,
  } = useConsoleStream(currentServerName)

  const handleJumpToBottom = useCallback(() => {
    const element = terminalRef.current
    if (element) {
      element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
    }
    setIsUserScrolled(false)
  }, [])

  const handleDownload = useCallback(() => {
    const text = lines.map((line) => line.text).join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `conan-console-${source}-${Date.now()}.txt`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => URL.revokeObjectURL(url), 100)
  }, [lines, source])

  return (
    <div className="flex flex-col h-full gap-4 animate-fade-in">
      <Card className="flex flex-col flex-1 min-h-0 border-border/60">
        <CardHeader className="pb-0 border-b border-border/50">
          <div className="flex items-center gap-2 mb-0">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-base">{copy.console.title}</CardTitle>
          </div>
          <ConsoleToolbar
            source={source}
            onSourceChange={setSource}
            filter={filter}
            onFilterChange={setFilter}
            onClear={clear}
            onDownload={handleDownload}
            onJumpToBottom={handleJumpToBottom}
            isUserScrolled={isUserScrolled}
          />
        </CardHeader>

        <CardContent className="flex flex-col flex-1 min-h-0 p-0">
          <ConsoleTerminal
            lines={filteredLines}
            terminalRef={terminalRef}
            onScrollChange={setIsUserScrolled}
            emptyMessage={currentServerName ? copy.console.waiting : copy.console.noServer}
          />
          <ConsoleStatusBar
            status={status}
            errorMessage={errorMessage}
            lineCount={lineCount}
            filteredCount={filteredLines.length}
            source={source}
            onReconnect={connect}
          />
        </CardContent>
      </Card>
    </div>
  )
}
