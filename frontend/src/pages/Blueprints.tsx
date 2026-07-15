import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  Boxes,
  BookOpen,
  Download,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from 'lucide-react'
import { api } from '@/api/client'
import { apiUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import type { BlueprintListEntry } from '@/types'

/** Hilfsfunktion: lesbarer Label pro source_type */
function sourceLabel(src: string): string {
  if (src === 'steam') return 'Steam'
  if (src === 'http') return 'HTTP'
  if (src === 'dockerOnly') return 'Docker'
  return src
}

/** Hilfsfunktion: lesbarer Label pro category */
function categoryLabel(cat: string): string {
  return cat
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Panel-Verwaltung fuer Blueprints: durchsuchbare Card-Liste + Aktionen.
 *
 *  RBAC:
 *  - Liste: ``panel.settings.read`` (Route-Gate in App.tsx).
 *  - Ersetzen / Loeschen / Neu hochladen: ``panel.settings.write``
 *    (UI-Gate hier + Backend prueft erneut).
 */
export function Blueprints() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [entries, setEntries] = useState<BlueprintListEntry[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [originFilter, setOriginFilter] = useState<'all' | 'native' | 'community'>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const newFileRef = useRef<HTMLInputElement | null>(null)
  const replaceFileRef = useRef<HTMLInputElement | null>(null)
  const replaceTargetIdRef = useRef<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api<{ blueprints: BlueprintListEntry[] }>('/blueprints')
      setEntries(res.blueprints)
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.loadFailed')
      toast.error(message)
      setEntries([])
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  // Einzigartige Kategorien für Filter-Dropdown
  const categories = useMemo<string[]>(() => {
    if (!entries) return []
    return Array.from(new Set(entries.map((e) => e.category))).sort()
  }, [entries])

  // Gefilterte + gesuchte Liste
  const filtered = useMemo<BlueprintListEntry[]>(() => {
    if (!entries) return []
    const q = search.toLowerCase()
    return entries.filter((e) => {
      if (originFilter !== 'all' && e.origin !== originFilter) return false
      if (categoryFilter !== 'all' && e.category !== categoryFilter) return false
      if (q) {
        const hit =
          e.name.toLowerCase().includes(q) ||
          e.id.toLowerCase().includes(q) ||
          (e.description ?? '').toLowerCase().includes(q) ||
          (e.author ?? '').toLowerCase().includes(q)
        if (!hit) return false
      }
      return true
    })
  }, [entries, search, originFilter, categoryFilter])

  const uploadFile = async (file: File, expectedId: string | null) => {
    const text = await file.text()
    // Strip // and /* */ comments without touching string literals
    // eslint-disable-next-line no-control-regex
    const strippedText = text.replace(/"(?:[^"\\]|\\.)*"|(\/\/[^\n]*|\/\*[\s\S]*?\*\/)/g, (m, g) => (g ? '' : m))

    let body: unknown
    try {
      body = JSON.parse(strippedText)
    } catch {
      toast.error(t('blueprints.uploadInvalidJson'))
      return
    }
    if (expectedId) {
      const incomingId =
        body && typeof body === 'object' && 'meta' in body
          ? ((body as { meta?: { id?: unknown } }).meta?.id ?? null)
          : null
      if (incomingId !== expectedId) {
        toast.error(t('blueprints.replaceIdMismatch', { expected: expectedId }))
        return
      }
    }
    try {
      const res = await api<{ id: string }>('/blueprints/import', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      toast.success(t('blueprints.uploadSuccess', { id: res.id }))
      await load()
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.uploadFailed')
      toast.error(message)
    }
  }

  const onNewFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy('new')
    try {
      await uploadFile(file, null)
    } finally {
      setBusy(null)
    }
  }

  const onReplaceFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    const targetId = replaceTargetIdRef.current
    replaceTargetIdRef.current = null
    if (!file || !targetId) return
    setBusy(`replace:${targetId}`)
    try {
      await uploadFile(file, targetId)
    } finally {
      setBusy(null)
    }
  }

  const handleReplace = (id: string) => {
    replaceTargetIdRef.current = id
    replaceFileRef.current?.click()
  }

  const handleDelete = async (entry: BlueprintListEntry) => {
    const ok = await confirm({
      title: t('blueprints.deleteConfirmTitle'),
      message: t('blueprints.deleteConfirmBody', { name: entry.name, id: entry.id }),
      confirmText: t('blueprints.deleteConfirm'),
      danger: true,
    })
    if (!ok) return
    setBusy(`delete:${entry.id}`)
    try {
      await api<void>(`/blueprints/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
      toast.success(t('blueprints.deleteSuccess', { id: entry.id }))
      await load()
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.deleteFailed')
      toast.error(message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-6xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <Boxes className="w-8 h-8 text-primary" />
        <h1 className="font-headline text-display-sm font-extrabold text-on-surface">
          {t('blueprints.pageTitle')}
        </h1>
      </div>
      <p className="font-body-md text-body-md text-on-surface-variant mb-6">
        {t('blueprints.pageSubtitle')}
      </p>

      {/* Help Banner: Host custom blueprints & Discord bots */}
      <div className="mb-6 bg-primary/10 border border-primary/20 rounded-md p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h4 className="font-bold text-on-surface text-sm">
            {t('blueprints.docsBannerTitle')}
          </h4>
          <p className="text-xs text-on-surface-variant mt-1">
            {t('blueprints.docsBannerText')}
          </p>
        </div>
        <Link
          to="/docs#docs-howto"
          className="msm-btn-secondary py-1.5 px-3 text-xs shrink-0 self-start sm:self-center inline-flex items-center gap-1.5"
        >
          <BookOpen className="w-3.5 h-3.5" />
          {t('blueprints.docsBannerBtn')}
        </Link>
      </div>

      {/* Toolbar: Upload + Suche + Filter */}
      <div className="flex flex-wrap gap-3 mb-6 items-start">
        {canWrite && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => newFileRef.current?.click()}
              disabled={busy === 'new'}
              className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2 disabled:opacity-50"
              data-testid="blueprints-upload-new"
            >
              {busy === 'new' ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Upload className="w-4 h-4" />
              )}
              {t('blueprints.uploadNew')}
            </button>
            <input
              ref={newFileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={onNewFileSelected}
              data-testid="blueprints-upload-new-input"
            />
            <input
              ref={replaceFileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={onReplaceFileSelected}
              data-testid="blueprints-replace-input"
            />
          </div>
        )}

        {/* Suche */}
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant/50 pointer-events-none" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('blueprints.search')}
            className="w-full pl-9 pr-3 py-2 rounded-md bg-surface-container text-on-surface border border-outline-variant/50 focus:outline-none focus:ring-1 focus:ring-primary font-body-md text-sm"
          />
        </div>

        {/* Origin-Filter */}
        <div className="flex rounded-md overflow-hidden border border-outline-variant/50 text-sm">
          {(['all', 'native', 'community'] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setOriginFilter(f)}
              className={`px-3 py-2 font-label-md transition-colors ${
                originFilter === f
                  ? 'bg-primary text-on-primary'
                  : 'bg-surface-container text-on-surface-variant hover:bg-surface-container-highest'
              }`}
            >
              {f === 'all' ? t('blueprints.filterAll') : f === 'native' ? t('blueprints.filterNative') : t('blueprints.filterCommunity')}
            </button>
          ))}
        </div>

        {/* Kategorie-Filter */}
        {categories.length > 1 && (
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="px-3 py-2 rounded-md bg-surface-container text-on-surface border border-outline-variant/50 focus:outline-none focus:ring-1 focus:ring-primary font-body-md text-sm"
          >
            <option value="all">{t('blueprints.filterCategory')}: {t('blueprints.filterAll')}</option>
            {categories.map((c) => (
              <option key={c} value={c}>{categoryLabel(c)}</option>
            ))}
          </select>
        )}
      </div>

      {/* Lade-State */}
      {entries === null && (
        <div className="p-6 text-on-surface-variant">{t('blueprints.loading')}</div>
      )}

      {/* Keine Ergebnisse */}
      {entries !== null && filtered.length === 0 && (
        <div className="msm-card p-10 text-center text-on-surface-variant">
          <Boxes className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="font-body-md text-sm">{t('blueprints.noResults')}</p>
        </div>
      )}

      {/* Blueprint-Cards Grid */}
      {filtered.length > 0 && (
        <>
          <p className="font-body-md text-xs text-on-surface-variant/60 mb-3">
            {filtered.length} / {entries?.length ?? 0}
          </p>
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}
            data-testid="blueprints-list"
          >
            {filtered.map((entry) => {
              const isNative = entry.origin === 'native'
              const isDeleting = busy === `delete:${entry.id}`
              const isReplacing = busy === `replace:${entry.id}`

              return (
                <div
                  key={entry.id}
                  className="msm-card p-5 flex flex-col gap-3 hover:border-primary/30 transition-colors"
                  data-testid={`blueprint-row-${entry.id}`}
                >
                  {/* Name + Origin-Badge */}
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-headline text-body-md font-bold text-on-surface leading-tight">
                      {entry.name}
                    </h3>
                    <span className={`flex-shrink-0 inline-block rounded-full px-2 py-0.5 text-xs font-label-md ${
                      isNative
                        ? 'bg-primary/10 text-primary border border-primary/20'
                        : 'bg-surface-container-highest text-on-surface-variant border border-outline-variant/40'
                    }`}>
                      {isNative ? t('blueprints.originNative') : t('blueprints.originCommunity')}
                    </span>
                  </div>

                  {/* Description */}
                  {entry.description && (
                    <p className="font-body-md text-xs text-on-surface-variant line-clamp-2">
                      {entry.description}
                    </p>
                  )}

                  {/* Meta-Chips */}
                  <div className="flex flex-wrap gap-1.5">
                    <span className="inline-block px-2 py-0.5 rounded text-xs bg-surface-container-highest text-on-surface-variant border border-outline-variant/30">
                      {categoryLabel(entry.category)}
                    </span>
                    <span className="inline-block px-2 py-0.5 rounded text-xs bg-surface-container-highest text-on-surface-variant border border-outline-variant/30">
                      {sourceLabel(entry.source_type)}
                    </span>
                    {entry.supports_mods && (
                      <span className="inline-block px-2 py-0.5 rounded text-xs bg-primary/8 text-primary border border-primary/20">
                        Mods
                      </span>
                    )}
                    {entry.supports_steam_workshop && (
                      <span className="inline-block px-2 py-0.5 rounded text-xs bg-primary/8 text-primary border border-primary/20">
                        Workshop
                      </span>
                    )}
                  </div>

                  {/* ID — kompakt, copyable feel */}
                  <p className="font-mono-sm text-xs text-on-surface-variant/50 truncate" title={entry.id}>
                    {entry.id}
                  </p>

                  {/* Aktionen */}
                  <div className="flex items-center gap-2 mt-auto pt-1 border-t border-outline-variant/20">
                    <a
                      href={apiUrl(`/blueprints/${encodeURIComponent(entry.id)}`)}
                      download
                      className="msm-btn-secondary inline-flex items-center gap-1 px-3 py-1.5 text-xs"
                      data-testid={`blueprint-download-${entry.id}`}
                      title={t('blueprints.download')}
                    >
                      <Download className="w-3.5 h-3.5" />
                      {t('blueprints.download')}
                    </a>
                    {canWrite && !isNative && (
                      <>
                        <button
                          type="button"
                          onClick={() => handleReplace(entry.id)}
                          disabled={isReplacing}
                          className="msm-btn-secondary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-50"
                          data-testid={`blueprint-replace-${entry.id}`}
                          title={t('blueprints.replace')}
                        >
                          {isReplacing ? (
                            <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                          ) : (
                            <RefreshCw className="w-3.5 h-3.5" />
                          )}
                          {t('blueprints.replace')}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(entry)}
                          disabled={isDeleting}
                          className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded-md text-status-destructive hover:bg-status-destructive/10 transition-colors disabled:opacity-50 ml-auto"
                          data-testid={`blueprint-delete-${entry.id}`}
                          title={t('blueprints.delete')}
                        >
                          {isDeleting ? (
                            <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                          ) : (
                            <Trash2 className="w-3.5 h-3.5" />
                          )}
                          {t('blueprints.delete')}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
