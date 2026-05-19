import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Server } from 'lucide-react'
import toast from 'react-hot-toast'
import { serversApi } from '@/lib/api'
import type { ServersData } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useUiLanguage } from '@/lib/ui-language'

export default function ServerSelector() {
  const [open, setOpen] = useState(false)
  const { copy } = useUiLanguage()
  const queryClient = useQueryClient()

  const { data } = useQuery({
    queryKey: ['servers'],
    queryFn: () => serversApi.list(),
    staleTime: 30_000,
  })

  const selectMutation = useMutation({
    mutationFn: (name: string) => serversApi.select(name),
    onSuccess: (result) => {
      queryClient.setQueryData<ServersData | undefined>(['servers'], (previous) => {
        if (!previous) return previous
        return {
          ...previous,
          current: result.current_server,
        }
      })

      const queryKeysToRefresh = [
        ['servers'],
        ['language'],
        ['dashboard'],
        ['backups'],
        ['autorestart'],
        ['config-overview'],
        ['config-serverconfig'],
        ['config-file'],
        ['config-backup-content'],
        ['files'],
        ['mods'],
        ['console'],
        ['actions'],
      ] as const

      for (const queryKey of queryKeysToRefresh) {
        void queryClient.invalidateQueries({ queryKey: [...queryKey] })
      }
    },
    onError: () => {
      toast.error(copy.serverSelector.switchFailed)
    },
  })

  const servers = data?.servers ?? []
  const current = data?.current ?? null
  const hasServers = servers.length > 0

  const handleSelect = (name: string) => {
    if (selectMutation.isPending) return
    setOpen(false)
    if (name !== current) {
      selectMutation.mutate(name)
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}
        className={cn(
          'flex items-center gap-1.5 rounded-md border border-border bg-[var(--el-2)] px-2.5 py-1 text-xs text-foreground/80',
          'hover:bg-accent/10 hover:border-accent/40 transition-colors',
          !hasServers && 'opacity-50 cursor-not-allowed'
        )}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={!hasServers}
      >
        <Server className="h-3 w-3 text-accent" />
        <span className="max-w-[120px] truncate font-medium">
          {!hasServers ? copy.serverSelector.noServer : current ?? copy.serverSelector.selectServer}
        </span>
        {hasServers && <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />}
      </button>

      {open && (
        <>
          {/* backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-md border border-border bg-[var(--el-1)] shadow-lg"
            role="listbox"
            onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}
          >
            {servers.map((s) => (
              <button
                key={s.name}
                role="option"
                aria-selected={s.name === current}
                onClick={() => handleSelect(s.name)}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors',
                  'hover:bg-accent/10',
                  s.name === current && 'text-accent font-semibold',
                  s.name !== current && 'text-foreground/80',
                )}
              >
                <Server className="h-3 w-3 shrink-0" />
                <span className="truncate">{s.display_name ?? s.name}</span>
                {s.name === current && (
                  <span className="ml-auto text-[10px] text-accent">{copy.serverSelector.active}</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
