import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, Plus, Search, Trash2, ExternalLink, Package, Globe, Star, Users, HardDrive, GripVertical, ToggleLeft, ToggleRight } from 'lucide-react'
import { api } from '../api/client'
import { confirm } from '@/stores/confirmStore'

interface Mod {
  id: number
  server_id: number
  workshop_id: string
  name: string | null
  last_updated: string | null
  installed_version: number | null
  load_order: number | null
  auto_update: boolean
  enabled: boolean
  dependencies_json: string | null
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

export function ModManager() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [mods, setMods] = useState<Mod[]>([])
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [showAddModal, setShowAddModal] = useState(false)
  const [showSteamSearch, setShowSteamSearch] = useState(false)
  const [newWorkshopId, setNewWorkshopId] = useState('')
  const [newModName, setNewModName] = useState('')
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  // Steam Workshop Browser state
  const [steamQuery, setSteamQuery] = useState('')
  const [steamResults, setSteamResults] = useState<SteamMod[]>([])
  const [steamLoading, setSteamLoading] = useState(false)
  const [browserTab, setBrowserTab] = useState<BrowserTab>('trending')
  const [browserMods, setBrowserMods] = useState<SteamMod[]>([])
  const [browserLoading, setBrowserLoading] = useState(false)
  const loadedTabs = useRef<Set<BrowserTab>>(new Set())

  // Drag & drop state
  const dragId = useRef<number | null>(null)

  useEffect(() => {
    if (!id) return
    loadMods()
  }, [id])

  useEffect(() => {
    if (!showSteamSearch) return
    loadBrowserTab(browserTab)
  }, [showSteamSearch, browserTab])

  const loadMods = async () => {
    try {
      const data = await api<Mod[]>(`/mods/${id}`)
      setMods(data)
    } catch {
      setMessage({ type: 'error', text: t('mods.loadError', 'Konnte Mods nicht laden') })
    } finally {
      setLoading(false)
    }
  }

  const loadBrowserTab = async (tab: BrowserTab, forceReload = false) => {
    if (loadedTabs.current.has(tab) && !forceReload) return
    setBrowserLoading(true)
    try {
      const data = await api<SteamMod[]>(`/steam/workshop/popular?server_id=${id}&sort=${tab}&limit=20`)
      setBrowserMods(data)
      loadedTabs.current.add(tab)
    } catch {
      setBrowserMods([])
    } finally {
      setBrowserLoading(false)
    }
  }

  const switchTab = (tab: BrowserTab) => {
    setBrowserTab(tab)
    loadedTabs.current.delete(tab) // always reload on tab switch
    setBrowserMods([])
  }

  const searchSteam = async () => {
    if (!steamQuery.trim()) return
    setSteamLoading(true)
    try {
      const q = encodeURIComponent(steamQuery)
      const data = await api<SteamMod[]>(`/steam/workshop/search?server_id=${id}&query=${q}&per_page=20`)
      setSteamResults(data)
    } catch {
      setMessage({ type: 'error', text: t('mods.steamSearchError', 'Steam-Suche fehlgeschlagen') })
    } finally {
      setSteamLoading(false)
    }
  }

  const subscribeMod = async (workshopId: string, name?: string) => {
    // Backend nimmt workshop_id/name als Query-Params. Body bleibt leer; api()
    // injiziert X-CSRF-Token automatisch.
    const params = new URLSearchParams({ workshop_id: workshopId })
    if (name) params.set('name', name)
    await api<Mod>(`/mods/${id}?${params.toString()}`, { method: 'POST' })
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
      setMessage({ type: 'success', text: t('mods.added', 'Hinzugefügt') })
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || t('mods.addFailed', 'Hinzufügen fehlgeschlagen') })
    } finally {
      setAdding(false)
    }
  }

  const addSteamMod = async (workshopId: string, name?: string) => {
    setAdding(true)
    try {
      await subscribeMod(workshopId, name)
      await loadMods()
      setMessage({ type: 'success', text: t('mods.added', 'Hinzugefügt') })
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || t('mods.addFailed', 'Hinzufügen fehlgeschlagen') })
    } finally {
      setAdding(false)
    }
  }

  const removeMod = async (modId: number) => {
    if (!(await confirm({ message: t('mods.confirmRemove', 'Mod wirklich entfernen?'), danger: true, confirmText: t('common.delete') }))) return
    try {
      await api(`/mods/${id}/${modId}`, { method: 'DELETE' })
      await loadMods()
      setMessage({ type: 'success', text: t('mods.removed', 'Mod entfernt') })
    } catch {
      setMessage({ type: 'error', text: t('mods.removeFailed', 'Entfernen fehlgeschlagen') })
    }
  }

  const patchModFlag = async (modId: number, flag: 'auto_update' | 'enabled', value: boolean) => {
    // Backend nimmt Flags als Query-Param; api() injiziert CSRF.
    const params = new URLSearchParams({ [flag]: value ? 'true' : 'false' })
    await api<Mod>(`/mods/${id}/${modId}?${params.toString()}`, { method: 'PATCH' })
  }

  const toggleAutoUpdate = async (modId: number, current: boolean) => {
    try {
      await patchModFlag(modId, 'auto_update', !current)
      await loadMods()
    } catch {
      setMessage({ type: 'error', text: t('mods.updateSettingFailed', 'Update-Einstellung fehlgeschlagen') })
    }
  }

  const toggleEnabled = async (modId: number, current: boolean) => {
    try {
      await patchModFlag(modId, 'enabled', !current)
      await loadMods()
    } catch {
      setMessage({ type: 'error', text: t('mods.updateSettingFailed', 'Einstellung fehlgeschlagen') })
    }
  }

  // Drag & drop handlers
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

    const from = mods.findIndex(m => m.id === dragId.current)
    const to = mods.findIndex(m => m.id === targetId)
    if (from === -1 || to === -1) return

    const newOrder = [...mods]
    const [moved] = newOrder.splice(from, 1)
    newOrder.splice(to, 0, moved)
    setMods(newOrder) // optimistic update

    try {
      // Backend erwartet eine reine list[int] als Body, kein Wrapper-Objekt.
      const data = await api<Mod[]>(`/mods/${id}/reorder`, {
        method: 'POST',
        body: JSON.stringify(newOrder.map(m => m.id)),
      })
      setMods(data)
    } catch {
      setMessage({ type: 'error', text: t('mods.reorderFailed', 'Neusortierung fehlgeschlagen') })
      await loadMods()
    }
    dragId.current = null
  }

  const filteredMods = mods.filter(mod =>
    mod.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
    mod.workshop_id.includes(searchTerm)
  )

  const renderSteamModCard = (mod: SteamMod) => {
    const isAdded = mods.some(m => m.workshop_id === mod.publishedfileid)
    return (
      <div key={mod.publishedfileid} className="msm-card p-4 flex items-start gap-4">
        {mod.preview_url ? (
          <img src={mod.preview_url} alt={mod.title} className="w-16 h-16 rounded-lg object-cover flex-shrink-0" loading="lazy" />
        ) : (
          <div className="w-16 h-16 rounded-lg bg-surface-container-highest flex items-center justify-center flex-shrink-0">
            <Package className="w-6 h-6 text-on-surface-variant" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h4 className="font-headline text-sm text-on-surface truncate">{mod.title}</h4>
          <p className="font-body-md text-xs text-on-surface-variant mt-1 line-clamp-2">{mod.description}</p>
          <div className="flex items-center gap-3 mt-2 text-xs text-on-surface-variant font-mono-sm">
            <span className="flex items-center gap-1"><Users className="w-3 h-3" /> {mod.subscriptions}</span>
            <span className="flex items-center gap-1"><Star className="w-3 h-3" /> {mod.favorites}</span>
            {mod.file_size_mb > 0 && (
              <span className="flex items-center gap-1"><HardDrive className="w-3 h-3" /> {mod.file_size_mb} MB</span>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-2 flex-shrink-0">
          <button
            onClick={() => addSteamMod(mod.publishedfileid, mod.title)}
            disabled={adding || isAdded}
            className="px-3 py-1.5 bg-primary hover:bg-primary/80 disabled:bg-surface-container-highest disabled:text-on-surface-variant text-on-primary rounded-md text-sm font-body-md transition-colors"
          >
            {isAdded ? t('mods.added', 'Hinzugefügt') : t('mods.add', 'Hinzufügen')}
          </button>
          <a
            href={mod.direct_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-1 px-3 py-1.5 border border-outline hover:bg-surface-container text-on-surface-variant rounded-md text-sm transition-colors"
          >
            <ExternalLink className="w-3 h-3" />
            {t('mods.viewInWorkshop', 'Workshop')}
          </a>
        </div>
      </div>
    )
  }

  const BROWSER_TABS: { key: BrowserTab; label: string }[] = [
    { key: 'trending', label: t('mods.tabTrending', 'Trending') },
    { key: 'popular', label: t('mods.tabPopular', 'Beliebt') },
    { key: 'newest', label: t('mods.tabNewest', 'Neueste') },
    { key: 'updated', label: t('mods.tabUpdated', 'Aktualisiert') },
  ]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate(`/servers/${id}`)}
            className="p-2 rounded-md border border-outline bg-surface-container-highest hover:bg-surface-container text-on-surface transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="font-headline text-headline-sm text-primary">{t('mods.title')}</h1>
            <p className="font-body-md text-sm text-on-surface-variant">{t('mods.subtitle')}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { loadedTabs.current.clear(); setShowSteamSearch(true) }}
            className="msm-btn-secondary flex items-center gap-2 px-4 py-2"
          >
            <Globe className="w-4 h-4" />
            {t('mods.searchSteam')}
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2"
          >
            <Plus className="w-4 h-4" />
            {t('mods.addMod')}
          </button>
        </div>
      </div>

      {message && (
        <div className={`p-3 rounded-md border text-sm font-body-md ${
          message.type === 'success'
            ? 'bg-status-success/10 border-status-success/30 text-status-success'
            : 'bg-status-error/10 border-status-error/30 text-status-error'
        }`}>
          {message.text}
        </div>
      )}

      {/* Local Search */}
      <div className="relative max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
        <input
          type="text"
          placeholder={t('mods.searchPlaceholder')}
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="msm-input pl-10"
        />
      </div>

      {/* Installed Mods List */}
      <div className="space-y-2">
        {loading ? (
          <div className="text-center py-12 text-on-surface-variant font-body-md">{t('common.loading')}</div>
        ) : filteredMods.length === 0 ? (
          <div className="msm-card p-12 text-center">
            <Package className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
            <h3 className="font-headline text-body-lg text-on-surface mb-2">
              {searchTerm ? t('mods.noSearchResults') : t('mods.noMods')}
            </h3>
            <p className="font-body-md text-sm text-on-surface-variant mb-4">
              {searchTerm ? t('mods.searchHint') : t('mods.noModsHint')}
            </p>
            {!searchTerm && (
              <button
                onClick={() => { loadedTabs.current.clear(); setShowSteamSearch(true) }}
                className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
              >
                <Globe className="w-4 h-4" />
                {t('mods.searchSteam')}
              </button>
            )}
          </div>
        ) : (
          filteredMods.map((mod) => (
            <div
              key={mod.id}
              className={`msm-card p-4 transition-opacity ${mod.enabled ? '' : 'opacity-60'}`}
              onDragOver={onDragOver}
              onDrop={(e) => onDrop(e, mod.id)}
            >
              <div className="flex items-center justify-between gap-3">
                {/* Drag handle */}
                <div
                  className="cursor-grab active:cursor-grabbing p-1 text-on-surface-variant hover:text-on-surface flex-shrink-0"
                  draggable
                  onDragStart={(e) => onDragStart(e, mod.id)}
                >
                  <GripVertical className="w-4 h-4" />
                </div>

                {/* Mod info */}
                <div className="flex-1 min-w-0">
                  <h3 className={`font-headline text-body-md ${mod.enabled ? 'text-on-surface' : 'text-on-surface-variant line-through'}`}>
                    {mod.name || `Workshop Mod ${mod.workshop_id}`}
                  </h3>
                  <div className="flex items-center gap-4 mt-1 text-sm text-on-surface-variant font-body-md">
                    <span>ID: {mod.workshop_id}</span>
                    {mod.last_updated && (
                      <span>{new Date(mod.last_updated).toLocaleDateString()}</span>
                    )}
                    {mod.load_order !== null && (
                      <span>{t('mods.loadOrder')}: {mod.load_order}</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  {/* Enable / Disable toggle */}
                  <button
                    onClick={() => toggleEnabled(mod.id, mod.enabled)}
                    title={mod.enabled ? t('mods.disable', 'Deaktivieren') : t('mods.enable', 'Aktivieren')}
                    className="p-1.5 rounded-md hover:bg-surface-container transition-colors"
                  >
                    {mod.enabled
                      ? <ToggleRight className="w-5 h-5 text-primary" />
                      : <ToggleLeft className="w-5 h-5 text-on-surface-variant" />
                    }
                  </button>

                  {/* Link to Workshop */}
                  <a
                    href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${mod.workshop_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-1.5 rounded-md hover:bg-surface-container transition-colors"
                    title={t('mods.viewInWorkshop')}
                  >
                    <ExternalLink className="w-4 h-4 text-on-surface-variant" />
                  </a>

                  {/* Auto-update */}
                  <label className="flex items-center gap-1.5 px-2 py-1.5 rounded-md border border-outline cursor-pointer hover:bg-surface-container transition-colors">
                    <input
                      type="checkbox"
                      checked={mod.auto_update}
                      onChange={() => toggleAutoUpdate(mod.id, mod.auto_update)}
                      className="rounded border-outline bg-surface-container-highest text-secondary focus:ring-secondary"
                    />
                    <span className="text-sm text-on-surface font-body-md">{t('mods.autoUpdate')}</span>
                  </label>

                  {/* Remove */}
                  <button
                    onClick={() => removeMod(mod.id)}
                    className="p-1.5 rounded-md hover:bg-status-error/10 transition-colors"
                    title={t('mods.remove')}
                  >
                    <Trash2 className="w-4 h-4 text-status-error" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

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
                onClick={addMod}
                disabled={adding || !newWorkshopId.trim()}
                className="msm-btn-primary flex-1 px-4 py-2 disabled:opacity-50"
              >
                {adding ? t('common.loading') : t('mods.add')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Steam Workshop Browser Modal */}
      {showSteamSearch && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card max-w-2xl w-full p-6 max-h-[90vh] flex flex-col">
            {/* Modal header */}
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="font-headline text-headline-md text-primary">{t('mods.steamSearch')}</h2>
                <p className="font-body-md text-sm text-on-surface-variant">{t('mods.steamSearchHint')}</p>
              </div>
              <button onClick={() => setShowSteamSearch(false)} className="msm-btn-secondary p-2">
                {t('common.close')}
              </button>
            </div>

            {/* Search bar + optional tag filter */}
            <div className="flex gap-2 mb-3">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
                <input
                  type="text"
                  placeholder={t('mods.searchPlaceholder')}
                  value={steamQuery}
                  onChange={(e) => setSteamQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && searchSteam()}
                  className="msm-input pl-10"
                />
              </div>
              <button
                onClick={searchSteam}
                disabled={steamLoading || !steamQuery.trim()}
                className="msm-btn-primary px-4 py-2 disabled:opacity-50"
              >
                {steamLoading ? t('common.loading') : t('common.search')}
              </button>
            </div>

            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto min-h-0">
              {/* Search Results */}
              {steamResults.length > 0 && (
                <div className="mb-6">
                  <h3 className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-3">
                    {t('mods.searchResults')}
                  </h3>
                  <div className="space-y-3">
                    {steamResults.map(mod => renderSteamModCard(mod))}
                  </div>
                </div>
              )}

              {/* Browser Tabs */}
              {steamResults.length === 0 && (
                <div>
                  {/* Tab bar */}
                  <div className="flex gap-1 mb-4 bg-surface-container rounded-lg p-1">
                    {BROWSER_TABS.map(tab => (
                      <button
                        key={tab.key}
                        onClick={() => switchTab(tab.key)}
                        className={`flex-1 px-3 py-1.5 rounded-md text-sm font-body-md transition-colors ${
                          browserTab === tab.key
                            ? 'bg-surface text-primary shadow-sm'
                            : 'text-on-surface-variant hover:text-on-surface'
                        }`}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>

                  {browserLoading ? (
                    <div className="text-center py-8 text-on-surface-variant font-body-md">{t('common.loading')}</div>
                  ) : browserMods.length > 0 ? (
                    <div className="space-y-3">
                      {browserMods.map(mod => renderSteamModCard(mod))}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-on-surface-variant font-body-md">
                      {t('mods.noSearchResults')}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
