import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Languages } from 'lucide-react'
import toast from 'react-hot-toast'
import { languageApi, serversApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { getUiCopy, useUiLanguage } from '@/lib/ui-language'

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'de', label: 'Deutsch' },
] as const

type LangCode = typeof LANGUAGES[number]['code']

export default function LanguageSelector() {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { copy } = useUiLanguage()
  const { data: serversData } = useQuery({
    queryKey: ['servers'],
    queryFn: () => serversApi.list(),
    staleTime: 30_000,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null

  const { data } = useQuery({
    queryKey: ['language', currentServer],
    queryFn: () => languageApi.get(),
    staleTime: 60_000,
    enabled: hasCurrentServer,
  })

  const rawLang = data?.language
  const current: LangCode = rawLang === 'de' ? 'de' : 'en'

  const mutation = useMutation({
    mutationFn: (lang: LangCode) => languageApi.set(lang),
    onSuccess: (res) => {
      queryClient.setQueryData(['language', currentServer], { language: res.language })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['autorestart', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['mods-autoupdate', currentServer] })
      const label = LANGUAGES.find((entry) => entry.code === res.language)?.label ?? res.language
      const nextCopy = getUiCopy(res.language === 'de' ? 'de' : 'en')
      toast.success(nextCopy.languageSelector.languageSet(label))
    },
    onError: () => {
      toast.error(copy.languageSelector.changeFailed)
    },
  })
  const currentLabel = LANGUAGES.find((entry) => entry.code === current)?.label ?? current.toUpperCase()

  const handleSelect = (code: LangCode) => {
    setOpen(false)
    if (hasCurrentServer && code !== current) {
      mutation.mutate(code)
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((isOpen) => !isOpen)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false)
        }}
        className={cn(
          'flex items-center gap-1.5 rounded-md border border-border bg-[var(--el-2)] px-2.5 py-1 text-xs text-foreground/80',
          'hover:bg-accent/10 hover:border-accent/40 transition-colors',
          !hasCurrentServer && 'opacity-50 cursor-not-allowed',
        )}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={mutation.isPending || !hasCurrentServer}
      >
        <Languages className="h-3 w-3 text-accent" />
        <span className="font-medium">{currentLabel}</span>
        <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute right-0 top-full z-50 mt-1 min-w-[130px] rounded-md border border-border bg-[var(--el-1)] shadow-lg"
            role="listbox"
            onKeyDown={(event) => {
              if (event.key === 'Escape') setOpen(false)
            }}
          >
            {LANGUAGES.map((lang) => (
              <button
                key={lang.code}
                role="option"
                aria-selected={lang.code === current}
                onClick={() => handleSelect(lang.code)}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors',
                  'hover:bg-accent/10',
                  lang.code === current && 'text-accent font-semibold',
                  lang.code !== current && 'text-foreground/80',
                )}
              >
                <span className="w-6 shrink-0 font-mono uppercase">{lang.code}</span>
                <span>{lang.label}</span>
                {lang.code === current && (
                  <span className="ml-auto text-[10px] text-accent">{copy.languageSelector.active}</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
