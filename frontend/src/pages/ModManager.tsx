import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown,
  ExternalLink,
  Globe,
  GripVertical,
  HardDrive,
  Package,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Star,
  ToggleLeft,
  ToggleRight,
  Trash2,
  Users,
} from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { getModInstallPresentation, hasActiveModInstall } from '@/services/modInstallStatus'

interface Mod {
  id: number
  server_id: number
  workshop_id: string
  name: string | null
  last_updated: string | null
  installed_version: number | null
  load_order: number | null
  enabled: boolean
  dependencies_json: string | null
  install_status: string
  install_action: string | null
  install_progress: number | null
  install_eta_seconds: number | null
  install_started_at: string | null
  install_completed_at: string | null
  install_error: string | null
  update_status: string
  update_reason: string | null
  update_checked_at: string | null
}

interface SteamMod {
  publishedfileid: string
  title: string
  description: string
  creator: string
  file_size_mb: number
  subscriptions: number
  favorites: number
  preview_url: string | null
  direct_url: string
  last_updated: string
}

type BrowserTab = 'trending' | 'popular' | 'newest' | 'updated'

const BROWSER_PAGE_SIZE = 24
const INSTALLED_PAGE_SIZE = 25

interface ModManagerProps {
  serverId: number
}

type BrowserCache = Partial<Record<BrowserTab, { mods: SteamMod[]; page: number; hasMore: boolean }>>

export function ModManager({ serverId }: ModManagerProps) {
  const { t } = useTranslation()
  const [mods, setMods] = useState<Mod[]>([])
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [newWorkshopId, setNewWorkshopId] = useState('')
  const [newModName, setNewModName] = useState('')
  const [adding, setAdding] = useState(false)
  const [reinstallingAll, setReinstallingAll] = useState(false)

  // Steam Workshop Browser (inline section)
  const [steamQuery, setSteamQuery] = useState('')
  const [steamResults, setSteamResults] = useState<SteamMod[]>([])
  const [steamPage, setSteamPage] = useState(1)
  const [steamHasMore, setSteamHasMore] = useState(false)
  const [steamLoading, setSteamLoading] = useState(false)
  const [browserTab, setBrowserTab] = useState<BrowserTab>('trending')
  // Cache pro Tab — verhindert Re-Fetch + Layout-Shift beim Tab-Wechsel,
  // dadurch bleibt die Scroll-Position erhalten.
  const [browserCache, setBrowserCache] = useState<BrowserCache>({})
  const [browserLoading, setBrowserLoading] = useState(false)
  const [installedShown, setInstalledShown] = useState(INSTALLED_PAGE_SIZE)

  // Drag & drop fuer Load-Order
  const dragId = useRef<number | null>(null)

  const loadMods = useCallback(
    async (options?: { silent?: boolean }) => {
      try {
        const data = await api<Mod[]>(`/mods/${serverId}`)
        setMods(data)
      } catch (err: unknown) {
        if (!options?.silent) {
          toast.error(err instanceof Error ? err.message : t('mods.loadError'))
        }
      } finally {
        if (!options?.silent) {
          setLoading(false)
        }
      }
    },
    [serverId, t],
  )

  useEffect(() => {
    setLoading(true)
    void loadMods()
  }, [loadMods])

  useEffect(() => {
    if (!mods.some(hasActiveModInstall)) return
    const intervalId = window.setInterval(() => {
      void loadMods({ silent: true })
    }, 2500)
    return () => window.clearInterval(intervalId)
  }, [loadMods, mods])

  useEffect(() => {
    if (browserCache[browserTab]) return // schon geladen — nicht neu fetchen
    void loadBrowserTab(browserTab, 1, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browserTab])

  const loadBrowserTab = async (tab: BrowserTab, page: number, append: boolean) => {
    setBrowserLoading(true)
    try {
      const data = await api<SteamMod[]>(
        `/steam/workshop/popular?server_id=${serverId}&sort=${tab}&limit=${BROWSER_PAGE_SIZE}&page=${page}`,
      )
      setBrowserCache((prev) => {
        const existing = prev[tab]?.mods ?? []
        return {
          ...prev,
          [tab]: {
            mods: append ? [...existing, ...data] : data,
            page,
            hasMore: data.length === BROWSER_PAGE_SIZE,
          },
        }
      })
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
      if (!append) {
        setBrowserCache((prev) => ({
          ...prev,
          [tab]: { mods: [], page: 1, hasMore: false },
        }))
      }
    } finally {
      setBrowserLoading(false)
    }
  }

  const switchTab = (tab: BrowserTab) => {
    setBrowserTab(tab) // Cache bleibt erhalten → Scroll-Position stabil
  }

  const loadMoreBrowser = () => {
    const entry = browserCache[browserTab]
    if (!entry || !entry.hasMore || browserLoading) return
    void loadBrowserTab(browserTab, entry.page + 1, true)
  }

  const searchSteam = async (page: number, append: boolean) => {
    if (!steamQuery.trim()) return
    setSteamLoading(true)
    try {
      const q = encodeURIComponent(steamQuery)
      const data = await api<SteamMod[]>(
        `/steam/workshop/search?server_id=${serverId}&query=${q}&per_page=${BROWSER_PAGE_SIZE}&page=${page}`,
      )
      setSteamResults((prev) => (append ? [...prev, ...data] : data))
      setSteamPage(page)
      setSteamHasMore(data.length === BROWSER_PAGE_SIZE)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.steamSearchError'))
    } finally {
      setSteamLoading(false)
    }
  }

  const runSearch = () => {
    void searchSteam(1, false)
  }

  const loadMoreSearch = () => {
    if (!steamHasMore || steamLoading) return
    void searchSteam(steamPage + 1, true)
  }

  const subscribeMod = async (workshopId: string, name?: string) => {
    const params = new URLSearchParams({ workshop_id: workshopId })
    if (name) params.set('name', name)
    await api<Mod>(`/mods/${serverId}?${params.toString()}`, { method: 'POST' })
  }

  const addMod = async () => {
    if (!newWorkshopId.trim()) return
    setAdding(true)
    try {
      await subscribeMod(newWorkshopId.trim(), newModName.trim() || undefined)
      await loadMods()
      setShowAddModal(false)
      setNewWorkshopId('')
      setNewModName('')
      toast.success(t('mods.added'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.addFailed'))
    } finally {
      setAdding(false)
    }
  }

  const addSteamMod = async (workshopId: string, name?: string) => {
    setAdding(true)
    try {
      await subscribeMod(workshopId, name)
      await loadMods()
      toast.success(t('mods.added'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.addFailed'))
    } finally {
      setAdding(false)
    }
  }

  const removeMod = async (modId: number) => {
    if (!(await confirm({ message: t('mods.confirmRemove'), danger: true, confirmText: t('common.delete') }))) return
    try {
      await api(`/mods/${serverId}/${modId}`, { method: 'DELETE' })
      await loadMods()
      toast.success(t('mods.removed'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.removeFailed'))
    }
  }

  const checkModUpdates = async () => {
    try {
      const data = await api<Mod[]>(`/mods/${serverId}/check-updates`, { method: 'POST' })
      setMods(data)
      toast.success(t('mods.updateCheckQueued'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.updateCheckFailed'))
    }
  }

  const abortModInstalls = async () => {
    if (!anyModInstallActive) return
    const ok = await confirm({
      message: t('mods.confirmAbortInstalls'),
      confirmText: t('mods.abortInstalls'),
      danger: true,
    })
    if (!ok) return
    try {
      const data = await api<Mod[]>(`/mods/${serverId}/abort-installs`, { method: 'POST' })
      setMods(data)
      toast.success(t('mods.abortInstallsDone'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.abortInstallsFailed'))
    }
  }

  const reinstallAllMods = async () => {
    if (mods.length === 0) return
    if (mods.some(hasActiveModInstall)) {
      toast.error(t('mods.reinstallAllBlocked'))
      return
    }
    const ok = await confirm({
      message: t('mods.confirmReinstallAll', { count: mods.length }),
      confirmText: t('mods.reinstallAll'),
    })
    if (!ok) return
    setReinstallingAll(true)
    try {
      const data = await api<Mod[]>(`/mods/${serverId}/reinstall-all`, { method: 'POST' })
      setMods(data)
      toast.success(t('mods.reinstallAllQueued'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.reinstallAllFailed'))
    } finally {
      setReinstallingAll(false)
    }
  }

  const installExistingMod = async (mod: Mod, action: 'update' | 'reinstall') => {
    if (action === 'reinstall') {
      const ok = await confirm({
        message: t('mods.confirmReinstall', { mod: mod.name || `Workshop Mod ${mod.workshop_id}` }),
        confirmText: t('mods.reinstall'),
      })
      if (!ok) return
    }
    try {
      await api<Mod>(`/mods/${serverId}/${mod.id}/install?action=${action}`, { method: 'POST' })
      await loadMods()
      toast.success(action === 'update' ? t('mods.updateQueued') : t('mods.reinstallQueued'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.installActionFailed'))
    }
  }

  const patchModFlag = async (modId: number, flag: 'enabled', value: boolean) => {
    const params = new URLSearchParams({ [flag]: value ? 'true' : 'false' })
    await api<Mod>(`/mods/${serverId}/${modId}?${params.toString()}`, { method: 'PATCH' })
  }

  const toggleEnabled = async (modId: number, current: boolean) => {
    try {
      await patchModFlag(modId, 'enabled', !current)
      await loadMods()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.updateSettingFailed'))
    }
  }

  // Drag & drop fuer Load-Order ──────────────────────────────────────────
  const onDragStart = (e: React.DragEvent, modId: number) => {
    dragId.current = modId
    e.dataTransfer.effectAllowed = 'move'
  }

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }

  const onDrop = async (e: React.DragEvent, targetId: number) => {
    e.preventDefault()
    if (dragId.current === null || dragId.current === targetId) return

    const from = mods.findIndex((m) => m.id === dragId.current)
    const to = mods.findIndex((m) => m.id === targetId)
    if (from === -1 || to === -1) return

    const newOrder = [...mods]
    const [moved] = newOrder.splice(from, 1)
    newOrder.splice(to, 0, moved)
    setMods(newOrder)

    try {
      const data = await api<Mod[]>(`/mods/${serverId}/reorder`, {
        method: 'POST',
        body: JSON.stringify(newOrder.map((m) => m.id)),
      })
      setMods(data)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('mods.reorderFailed'))
      await loadMods()
    }
    dragId.current = null
  }

  const filteredMods = mods.filter(
    (mod) =>
      mod.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      mod.workshop_id.includes(searchTerm),
  )
  const visibleInstalled = filteredMods.slice(0, installedShown)
  const hasMoreInstalled = filteredMods.length > visibleInstalled.length
  const anyModInstallActive = mods.some(hasActiveModInstall)

  // Suche zeigt Suchtreffer-Grid, sonst das gewaehlte Browser-Tab-Grid.
  const isSearchMode = steamResults.length > 0
  const browserEntry = browserCache[browserTab]
  const displayedMods = isSearchMode ? steamResults : browserEntry?.mods ?? []
  const showLoadMoreBrowser = !isSearchMode && !!browserEntry?.hasMore
  const showLoadMoreSearch = isSearchMode && steamHasMore
  const statusBadgeClass = {
    success: 'msm-badge-success',
    warning: 'msm-badge-warning',
    info: 'msm-badge-info',
    error: 'msm-badge-error',
  } as const

  const renderSteamModCard = (mod: SteamMod) => {
    const isAdded = mods.some((m) => m.workshop_id === mod.publishedfileid)
    return (
      <div
        key={mod.publishedfileid}
        className="msm-card overflow-hidden flex flex-col hover:border-mint-accent/40 transition-all"
      >
        {mod.preview_url ? (
          <img
            src={mod.preview_url}
            alt={mod.title}
            className="w-full h-40 object-cover bg-surface-container-highest"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-40 flex items-center justify-center bg-surface-container-highest">
            <Package className="w-10 h-10 text-on-surface-variant" />
          </div>
        )}
        <div className="p-4 flex flex-col gap-2 flex-1">
          <h4 className="font-headline text-body-md text-on-surface line-clamp-2 leading-tight">
            {mod.title}
          </h4>
          <p className="font-body-md text-xs text-on-surface-variant line-clamp-3 flex-1">
            {mod.description}
          </p>
          <div className="flex flex-wrap items-center gap-3 text-xs text-on-surface-variant font-mono-sm">
            <span className="flex items-center gap-1">
              <Users className="w-3 h-3" /> {mod.subscriptions.toLocaleString()}
            </span>
            <span className="flex items-center gap-1">
              <Star className="w-3 h-3" /> {mod.favorites.toLocaleString()}
            </span>
            {mod.file_size_mb > 0 && (
              <span className="flex items-center gap-1">
                <HardDrive className="w-3 h-3" /> {mod.file_size_mb} MB
              </span>
            )}
          </div>
          <div className="flex gap-2 mt-1">
            <button
              onClick={() => addSteamMod(mod.publishedfileid, mod.title)}
              disabled={adding || isAdded}
              className="msm-btn-primary flex-1 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {isAdded ? t('mods.added') : t('mods.add')}
            </button>
            <a
              href={mod.direct_url}
              target="_blank"
              rel="noopener noreferrer"
              className="msm-btn-secondary px-3 py-1.5 text-sm inline-flex items-center gap-1.5"
              title={t('mods.viewInWorkshop')}
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>
      </div>
    )
  }

  const BROWSER_TABS: { key: BrowserTab; label: string }[] = [
    { key: 'trending', label: t('mods.tabTrending') },
    { key: 'popular', label: t('mods.tabPopular') },
    { key: 'newest', label: t('mods.tabNewest') },
    { key: 'updated', label: t('mods.tabUpdated') },
  ]

  return (
    <div className="space-y-8">
      {/* Tab-Body Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="font-body-md text-body-md text-on-surface-variant">{t('mods.subtitle')}</p>
        <button
          onClick={() => setShowAddModal(true)}
          className="msm-btn-secondary flex items-center gap-2 px-3 py-2 text-sm"
        >
          <Plus className="w-4 h-4" />
          {t('mods.addById')}
        </button>
      </div>

      {/* Installed Mods Section */}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="font-headline text-body-lg text-on-surface inline-flex items-center gap-2">
            <Package className="w-4 h-4 text-secondary" />
            {t('mods.installedTitle')}
            <span className="text-on-surface-variant text-sm font-mono">({mods.length})</span>
          </h2>
          <div className="relative w-full sm:w-72">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
            <input
              type="search"
              placeholder={t('mods.searchPlaceholder')}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="msm-input pl-10 text-sm"
            />
          </div>
          <button
            onClick={() => void abortModInstalls()}
            disabled={loading || !anyModInstallActive}
            className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2 disabled:opacity-50"
            title={t('mods.abortInstallsHint')}
          >
            {t('mods.abortInstalls')}
          </button>
          <button
            onClick={() => void reinstallAllMods()}
            disabled={loading || reinstallingAll || mods.length === 0 || anyModInstallActive}
            className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2 disabled:opacity-50"
            title={t('mods.reinstallAllHint')}
          >
            <RotateCcw className={`w-4 h-4 ${reinstallingAll ? 'animate-spin' : ''}`} />
            {t('mods.reinstallAll')}
          </button>
          <button
            onClick={() => void checkModUpdates()}
            className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" />
            {t('mods.checkUpdates')}
          </button>
        </div>

        <div className="space-y-2">
          {loading ? (
            <div className="text-center py-12 text-on-surface-variant font-body-md">
              {t('common.loading')}
            </div>
          ) : filteredMods.length === 0 ? (
            <div className="msm-card p-10 text-center border-dashed border-2 border-outline-variant">
              <Package className="w-10 h-10 text-on-surface-variant mx-auto mb-3" />
              <h3 className="font-headline text-body-lg text-on-surface mb-2">
                {searchTerm ? t('mods.noSearchResults') : t('mods.noMods')}
              </h3>
              <p className="font-body-md text-sm text-on-surface-variant">
                {searchTerm ? t('mods.searchHint') : t('mods.noModsHint')}
              </p>
            </div>
          ) : (
            visibleInstalled.map((mod) => {
              const installStatus = getModInstallPresentation(mod, t)
              const isInstalling = hasActiveModInstall(mod)
              const hasPendingUpdate =
                (mod.install_status === 'pending' && mod.install_action === 'update') ||
                mod.update_status === 'outdated'
              return (
              <div
                key={mod.id}
                className={`msm-card p-4 transition-opacity ${mod.enabled ? '' : 'opacity-60'}`}
                onDragOver={onDragOver}
                onDrop={(e) => void onDrop(e, mod.id)}
              >
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div
                    className="cursor-grab active:cursor-grabbing p-1 text-on-surface-variant hover:text-on-surface flex-shrink-0"
                    draggable
                    onDragStart={(e) => onDragStart(e, mod.id)}
                  >
                    <GripVertical className="w-4 h-4" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <h3
                      className={`font-headline text-body-md ${mod.enabled ? 'text-on-surface' : 'text-on-surface-variant line-through'}`}
                    >
                      {mod.name || `Workshop Mod ${mod.workshop_id}`}
                    </h3>
                    <div className="flex flex-wrap items-center gap-4 mt-1 text-sm text-on-surface-variant font-body-md">
                      <span>ID: {mod.workshop_id}</span>
                      {mod.last_updated && (
                        <span>{new Date(mod.last_updated).toLocaleDateString()}</span>
                      )}
                      {mod.load_order !== null && (
                        <span>
                          {t('mods.loadOrder')}: {mod.load_order}
                        </span>
                      )}
                    </div>
                    <div className="mt-2 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={statusBadgeClass[installStatus.kind]}>
                          {installStatus.label}
                        </span>
                        {installStatus.detail && (
                          <span className="text-xs text-on-surface-variant font-body-md">
                            {installStatus.detail}
                          </span>
                        )}
                      </div>
                      {installStatus.showProgress && (
                        <div className="h-1.5 w-full max-w-md overflow-hidden rounded-full bg-surface-container-high border border-outline-variant">
                          <div
                            className="h-full rounded-full bg-primary transition-[width] duration-500"
                            style={{ width: `${installStatus.progress ?? 0}%` }}
                          />
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {hasPendingUpdate && (
                      <button
                        onClick={() => void installExistingMod(mod, 'update')}
                        disabled={isInstalling}
                        className="msm-btn-primary px-3 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
                        title={t('mods.updateAvailable')}
                      >
                        <RefreshCw className="w-3.5 h-3.5" />
                        {t('mods.updateAvailable')}
                      </button>
                    )}
                    <button
                      onClick={() => void installExistingMod(mod, 'reinstall')}
                      disabled={isInstalling}
                      className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
                      title={t('mods.reinstall')}
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      {t('mods.reinstall')}
                    </button>
                    <button
                      onClick={() => toggleEnabled(mod.id, mod.enabled)}
                      title={mod.enabled ? t('mods.disable') : t('mods.enable')}
                      className="p-1.5 rounded-md hover:bg-surface-container transition-colors"
                    >
                      {mod.enabled ? (
                        <ToggleRight className="w-5 h-5 text-primary" />
                      ) : (
                        <ToggleLeft className="w-5 h-5 text-on-surface-variant" />
                      )}
                    </button>

                    <a
                      href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${mod.workshop_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="p-1.5 rounded-md hover:bg-surface-container transition-colors"
                      title={t('mods.viewInWorkshop')}
                    >
                      <ExternalLink className="w-4 h-4 text-on-surface-variant" />
                    </a>

                    <button
                      onClick={() => removeMod(mod.id)}
                      className="p-1.5 rounded-md hover:bg-status-destructive/10 transition-colors"
                      title={t('mods.remove')}
                    >
                      <Trash2 className="w-4 h-4 text-status-destructive" />
                    </button>
                  </div>
                </div>
              </div>
              )
            })
          )}
          {hasMoreInstalled && (
            <button
              onClick={() => setInstalledShown((n) => n + INSTALLED_PAGE_SIZE)}
              className="msm-btn-secondary w-full px-4 py-2 text-sm inline-flex items-center justify-center gap-2"
            >
              <ChevronDown className="w-4 h-4" />
              {t('mods.loadMore')} ({filteredMods.length - visibleInstalled.length})
            </button>
          )}
        </div>
      </section>

      {/* Steam Workshop Browser (Inline Section, breit) */}
      <section className="space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="font-headline text-body-lg text-on-surface inline-flex items-center gap-2">
            <Globe className="w-4 h-4 text-secondary" />
            {t('mods.steamSearch')}
          </h2>
        </div>

        <div className="flex gap-2 items-center flex-wrap">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
            <input
              type="search"
              placeholder={t('mods.searchPlaceholder')}
              value={steamQuery}
              onChange={(e) => setSteamQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              className="msm-input pl-10"
            />
          </div>
          <button
            onClick={runSearch}
            disabled={steamLoading || !steamQuery.trim()}
            className="msm-btn-primary px-4 py-2 disabled:opacity-50"
          >
            {steamLoading && steamPage === 1 ? t('common.loading') : t('common.search')}
          </button>
          {isSearchMode && (
            <button
              onClick={() => {
                setSteamResults([])
                setSteamQuery('')
                setSteamPage(1)
                setSteamHasMore(false)
              }}
              className="msm-btn-secondary px-3 py-2 text-sm"
            >
              {t('mods.clearSearch')}
            </button>
          )}
        </div>

        {/* Tabs nur ausserhalb des Suchmodus. Nicht sticky, damit der Scroll-
            Anker (das Tab-Element) seine Position behaelt und der Browser beim
            Wechsel zwischen Tabs nicht nach oben springt. */}
        {!isSearchMode && (
          <div className="flex gap-1 bg-surface-container rounded-lg p-1 overflow-x-auto">
            {BROWSER_TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => switchTab(tab.key)}
                className={`flex-1 min-w-[110px] px-3 py-1.5 rounded-md text-sm font-body-md transition-colors ${
                  browserTab === tab.key
                    ? 'bg-surface text-primary shadow-sm'
                    : 'text-on-surface-variant hover:text-on-surface'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}

        {displayedMods.length > 0 ? (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {displayedMods.map((mod) => renderSteamModCard(mod))}
            </div>
            {(showLoadMoreBrowser || showLoadMoreSearch) && (
              <div className="flex justify-center pt-2">
                <button
                  onClick={isSearchMode ? loadMoreSearch : loadMoreBrowser}
                  disabled={browserLoading || steamLoading}
                  className="msm-btn-secondary px-4 py-2 text-sm inline-flex items-center gap-2 disabled:opacity-50"
                >
                  <ChevronDown className="w-4 h-4" />
                  {isSearchMode
                    ? steamLoading
                      ? t('common.loading')
                      : t('mods.loadMore')
                    : browserLoading
                      ? t('common.loading')
                      : t('mods.loadMore')}
                </button>
              </div>
            )}
          </>
        ) : browserLoading || steamLoading ? (
          <div className="text-center py-12 text-on-surface-variant font-body-md">
            {t('common.loading')}
          </div>
        ) : (
          <div className="text-center py-12 text-on-surface-variant font-body-md">
            {t('mods.noSearchResults')}
          </div>
        )}
      </section>

      {/* Add by ID Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card w-full max-w-md p-6">
            <h2 className="font-headline text-headline-md text-primary mb-4">{t('mods.addMod')}</h2>
            <div className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('mods.workshopId')} *
                </label>
                <input
                  type="text"
                  placeholder="z.B. 123456789"
                  value={newWorkshopId}
                  onChange={(e) => setNewWorkshopId(e.target.value)}
                  className="msm-input"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('mods.modName')}
                </label>
                <input
                  type="text"
                  placeholder="Mod-Name"
                  value={newModName}
                  onChange={(e) => setNewModName(e.target.value)}
                  className="msm-input"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button onClick={() => setShowAddModal(false)} className="msm-btn-secondary flex-1 px-4 py-2">
                {t('common.cancel')}
              </button>
              <button
                onClick={() => void addMod()}
                disabled={adding || !newWorkshopId.trim()}
                className="msm-btn-primary flex-1 px-4 py-2 disabled:opacity-50"
              >
                {adding ? t('common.loading') : t('mods.add')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
