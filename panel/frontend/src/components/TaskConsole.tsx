import { useState, useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ShieldCheck, Loader2, Plus } from 'lucide-react'
import { actionsApi } from '@/lib/api'
import type { Task } from '@/lib/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUiLanguage } from '@/lib/ui-language'

function getLastMeaningfulLogLine(log: string[]) {
  for (let i = log.length - 1; i >= 0; i -= 1) {
    const line = log[i]?.trim()
    if (!line || line.startsWith('--- Action:')) continue
    return line
  }
  return null
}

function TaskResult({ task, log }: { task: Task; log: string[] }) {
  const { copy } = useUiLanguage()
  const t = copy.taskConsole
  const summary = getLastMeaningfulLogLine(log)

  if (task.status === 'finished') {
    return (
      <div className="mt-2 space-y-1">
        <div className="text-emerald-500 font-bold underline">
          {t.actionCompleted}
        </div>
        {summary && (
          <div className="text-emerald-300">
            {t.summaryPrefix}: {summary}
          </div>
        )}
      </div>
    )
  }

  if (task.status !== 'failed' && task.status !== 'timeout') return null
  const steamErr = log.find(
    (l) =>
      l.includes('steamlogin_required') ||
      l.includes('valid steamlogin') ||
      l.includes('steampassword_required'),
  )
  const detail = steamErr
    ? t.credentialsRequired
    : task.error || summary || t.actionFailedCode(task.returncode)
  return (
    <div className="text-red-500 font-bold mt-2 underline uppercase tracking-tight">
      {t.errorPrefix}: {detail}
    </div>
  )
}

interface TaskConsoleProps {
  serverName: string
  initialTask?: Task | null
}

export default function TaskConsole({ serverName, initialTask }: TaskConsoleProps) {
  const { copy } = useUiLanguage()
  const t = copy.taskConsole
  const [log, setLog] = useState<string[]>([])
  const [isVisible, setIsVisible] = useState(Boolean(initialTask && initialTask.status !== 'finished'))
  const [maxProgress, setMaxProgress] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const completionTimeoutRef = useRef<number | null>(null)
  const seenRunningTaskKeysRef = useRef<Set<string>>(new Set())

  const { data } = useQuery({
    queryKey: ['action-status', serverName, 'default'],
    queryFn: () => actionsApi.status('default'),
    refetchInterval: (query: any) => {
      const task = query.state.data?.task
      return task?.status === 'running' || task?.status === 'started' ? 2000 : 30_000
    },
  })

  useEffect(() => {
    if (data) setLog(data.log ?? [])
  }, [data?.log])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [log])

  const task = data?.task || initialTask
  const taskKey = useMemo(
    () => (task ? `${task.action}:${task.started_at}` : null),
    [task?.action, task?.started_at],
  )
  const isRunning = task?.status === 'running'
  const isFailed = task?.status === 'failed'
  const isTimedOut = task?.status === 'timeout'
  const isFinished = task?.status === 'finished'
  const isErrored = isFailed || isTimedOut

  useEffect(() => {
    if (!task || !taskKey) {
      setIsVisible(false)
      return
    }

    if (completionTimeoutRef.current !== null && !isFinished) {
      window.clearTimeout(completionTimeoutRef.current)
      completionTimeoutRef.current = null
    }

    if (task.status === 'running') {
      seenRunningTaskKeysRef.current.add(taskKey)
      setIsVisible(true)
      return
    }

    if (task.status === 'failed' || task.status === 'timeout') {
      setIsVisible(true)
      return
    }

    if (task.status === 'finished') {
      if (!seenRunningTaskKeysRef.current.has(taskKey)) {
        setIsVisible(false)
        return
      }
      setIsVisible(true)
      completionTimeoutRef.current = window.setTimeout(() => {
        setIsVisible(false)
      }, 15000)
      return
    }

    setIsVisible(false)
  }, [task, taskKey, isFinished])

  useEffect(() => {
    return () => {
      if (completionTimeoutRef.current !== null) {
        window.clearTimeout(completionTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!task) return
    if (isRunning) {
      const joined = log.join('\n').toLowerCase()
      let computed = 20
      if (task.action === 'install' || task.action === 'update') {
        if (joined.includes('verifying')) computed = 80
        else if (joined.includes('update state')) computed = 60
        else if (joined.includes('downloading')) computed = 40
      } else {
        computed = 50
      }
      setMaxProgress((prev) => Math.max(prev, computed))
    } else if (isFinished) {
      setMaxProgress(0)
    }
  }, [isRunning, isFinished, log, task?.action])

  if (!task || !isVisible) return null

  const progress = isRunning ? Math.max(maxProgress, 20) : (isErrored || isFinished) ? 100 : 0

  return (
    <Card
      className={cn(
        'border-accent/40 bg-accent/5 overflow-hidden transition-all animate-in fade-in slide-in-from-top-4',
        isErrored && 'border-red-500/40 bg-red-500/5',
        isFinished && 'border-emerald-500/40 bg-emerald-500/5',
      )}
    >
      <CardHeader className="py-3 flex flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-3">
          {isRunning ? (
            <Loader2 className="h-4 w-4 animate-spin text-accent" />
          ) : isErrored ? (
            <AlertTriangle className="h-4 w-4 text-red-500" />
          ) : (
            <ShieldCheck className="h-4 w-4 text-emerald-500" />
          )}
          <CardTitle className="text-sm font-bold uppercase tracking-wider">
            {task.action} {isRunning ? t.statusRunning : isErrored ? t.statusFailed : t.statusCompleted}
          </CardTitle>
        </div>
        {(isFinished || isErrored) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsVisible(false)}
            className="h-8 w-8 p-0"
          >
            <Plus className="h-4 w-4 rotate-45" />
          </Button>
        )}
      </CardHeader>
      <CardContent className="p-0">
        <div
          ref={scrollRef}
          className="bg-black/90 text-emerald-400 font-mono text-[10px] p-4 h-48 overflow-y-auto border-y border-border/20 selection:bg-emerald-500/30"
        >
          {log.map((line, i) => (
            <div key={i} className="leading-relaxed">
              {line}
            </div>
          ))}
          {isRunning && !log.length && (
            <div className="flex items-center gap-2 opacity-50">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>{t.initializing}</span>
            </div>
          )}
          <TaskResult task={task} log={log} />
        </div>

        <div className="h-1.5 w-full bg-muted overflow-hidden">
          <div
            className={cn(
              'h-full transition-all duration-500 ease-out',
              isRunning ? 'bg-accent' : isErrored ? 'bg-red-500' : 'bg-emerald-500',
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
      </CardContent>
    </Card>
  )
}
