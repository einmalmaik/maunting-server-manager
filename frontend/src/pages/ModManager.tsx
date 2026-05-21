import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, Plus, Search, Trash2, ArrowUp, ArrowDown, ExternalLink, Package, Globe, Star, Users, HardDrive } from 'lucide-react'

interface Mod {
  id: number
  server_id: number
  workshop_id: string
  name: string | null
  last_updated: string | null
  installed_version: number | null
  load_order: number | null
  auto_update: boolean
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

  const [steamQuery, setSteamQuery] = useState('')
  const [steamResults, setSteamResults] = useState<SteamMod[]>([])
  const [steamLoading, setSteamLoading] = useState(false)
  const [popularMods, setPopularMods] = useState<SteamMod[]>([])
  const [popularLoading, setPopularLoading] = useState(false)

  useEffect(() => {
    if (!id) return
    loadMods()
    loadPopularMods()
  }, [id])

  const loadMods = async () => {
    try {
      const res = await fetch(`/api/mods/${id}`)
      if (!res.ok) throw new Error()
      setMods(await res.json())
    } catch {
      setMessage({ type: 'error', text: t('mods.loadError', 'Konnte Mods nicht laden') })
    } finally {
      setLoading(false)
    }
  }

  const loadPopularMods = async () => {
    setPopularLoading(true)
    try {
      const res = await fetch(`/api/steam/workshop/popular?server_id=${id}&limit=10`)
      if (!res.ok) throw new Error()
      setPopularMods(await res.json())
    } catch {
      // Silent fail
    } finally {
      setPopularLoading(false)
    }
  }

  const searchSteam = async () => {
    if (!steamQuery.trim()) return
    setSteamLoading(true)
    try {
      const res = await fetch(`/api/steam/workshop/search?server_id=${id}&query=${encodeURIComponent(steamQuery)}&per_page=20`)
      if (!res.ok) throw new Error()
      setSteamResults(await res.json())
    } catch {
      setMessage({ type: 'error', text: t('mods.steamSearchError', 'Steam-Suche fehlgeschlagen') })
    } finally {
      setSteamLoading(false)
    }
  }

  const addMod = async () => {
    if (!newWorkshopId.trim()) return
    setAdding(true)
    try {
      const res = await fetch(`/api/mods/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workshop_id: newWorkshopId.trim(),
          name: newModName.trim() || undefined,
        }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Fehler')
      }
      await loadMods()
      setShowAddModal(false)
      setNewWorkshopId('')
      setNewModName('')
      setMessage({ type: 'success', text: t('mods.added', 'Mod hinzugef&uuml;gt') })
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || t('mods.addFailed', 'Hinzuf&uuml;gen fehlgeschlagen') })
    } finally {
      setAdding(false)
    }
  }

  const addSteamMod = async (workshopId: string, name?: string) => {
    setAdding(true)
    try {
      const res = await fetch(`/api/mods/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workshop_id: workshopId,
          name: name || undefined,
        }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Fehler')
      }
      await loadMods()
      setMessage({ type: 'success', text: t('mods.added', 'Mod hinzugef&uuml;gt') })
    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || t('mods.addFailed', 'Hinzuf&uuml;gen fehlgeschlagen') })
    } finally {
      setAdding(false)
    }
  }

  const removeMod = async (modId: number) => {
    if (!confirm(t('mods.confirmRemove', 'Mod wirklich entfernen?'))) return
    try {
      const res = await fetch(`/api/mods/${id}/${modId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error()
      await loadMods()
      setMessage({ type: 'success', text: t('mods.removed', 'Mod entfernt') })
    } catch {
      setMessage({ type: 'error', text: t('mods.removeFailed', 'Entfernen fehlgeschlagen') })
    }
  }

  const toggleAutoUpdate = async (modId: number, current: boolean) => {
    try {
      const res = await fetch(`/api/mods/${id}/${modId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_update: !current }),
      })
      if (!res.ok) throw new Error()
      await loadMods()
    } catch {
      setMessage({ type: 'error', text: t('mods.updateSettingFailed', 'Update-Einstellung fehlgeschlagen') })
    }
  }

  const moveMod = async (modId: number, direction: 'up' | 'down') => {
    const currentIndex = mods.findIndex(m => m.id === modId)
    if (
      (direction === 'up' && currentIndex === 0) ||
      (direction === 'down' && currentIndex === mods.length - 1)
    ) return

    const newOrder = [...mods]
    const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1
    ;[newOrder[currentIndex], newOrder[targetIndex]] = [newOrder[targetIndex], newOrder[currentIndex]]

    try {
      const res = await fetch(`/api/mods/${id}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order: newOrder.map(m => m.id) }),
      })
      if (!res.ok) throw new Error()
      setMods(newOrder)
    } catch {
      setMessage({ type: 'error', text: t('mods.reorderFailed', 'Neusortierung fehlgeschlagen') })
    }
  }

  const filteredMods = mods.filter(mod =>
    (mod.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
     mod.workshop_id.includes(searchTerm))
  )

  const renderSteamModCard = (mod: SteamMod) => (
    <div key={mod.publishedfileid} className="msm-card p-4 flex items-start gap-4">
      {mod.preview_url ? (
        <img
          src={mod.preview_url}
          alt={mod.title}
          className="w-16 h-16 rounded-lg object-cover flex-shrink-0"
          loading="lazy"
        />
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
      <div className="flex flex-col gap-2">
        <button
          onClick={() => addSteamMod(mod.publishedfileid, mod.title)}
          disabled={adding || mods.some(m => m.workshop_id === mod.publishedfileid)}
          className="px-3 py-1.5 bg-primary hover:bg-primary/80 disabled:bg-surface-container-highest disabled:text-on-surface-variant text-on-primary rounded-md text-sm font-body-md transition-colors"
        >
          {mods.some(m => m.workshop_id === mod.publishedfileid) ? t('mods.added', 'Hinzugef&uuml;gt') : t('mods.add', 'Hinzuf&uuml;gen')}
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
            onClick={() => setShowSteamSearch(true)}
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
        <div className={`mb-4 p-3 rounded-md border text-sm font-body-md ${
          message.type === 'success'
            ? 'bg-status-success/10 border-status-success/30 text-status-success'
            : 'bg-status-error/10 border-status-error/30 text-status-error'
        }`}>
          {message.text}
        </div>
      )}

      {/* Local Search */}
      <div className="mb-6">
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
      </div>

      {/* Installed Mods List */}
      <div className="space-y-3 mb-8">
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
                onClick={() => setShowSteamSearch(true)}
                className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
              >
                <Globe className="w-4 h-4" />
                {t('mods.searchSteam')}
              </button>
            )}
          </div>
        ) : (
          filteredMods.map((mod, index) => (
            <div key={mod.id} className="msm-card p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="flex flex-col gap-1">
                    <button
                      onClick={() => moveMod(mod.id, 'up')}
                      disabled={index === 0}
                      className="p-1 rounded hover:bg-surface-container disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      <ArrowUp className="w-3 h-3 text-on-surface-variant" />
                    </button>
                    <button
                      onClick={() => moveMod(mod.id, 'down')}
                      disabled={index === filteredMods.length - 1}
                      className="p-1 rounded hover:bg-surface-container disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      <ArrowDown className="w-3 h-3 text-on-surface-variant" />
                    </button>
                  </div>

                  <div>
                    <h3 className="font-headline text-body-md text-on-surface">
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
                </div>

                <div className="flex items-center gap-2">
                  <a
                    href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${mod.workshop_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 rounded-md hover:bg-surface-container transition-colors"
                    title={t('mods.viewInWorkshop')}
                  >
                    <ExternalLink className="w-4 h-4 text-on-surface-variant" />
                  </a>

                  <label className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-outline cursor-pointer hover:bg-surface-container transition-colors">
                    <input
                      type="checkbox"
                      checked={mod.auto_update}
                      onChange={() => toggleAutoUpdate(mod.id, mod.auto_update)}
                      className="rounded border-outline bg-surface-container-highest text-secondary focus:ring-secondary"
                    />
                    <span className="text-sm text-on-surface font-body-md">{t('mods.autoUpdate')}</span>
                  </label>

                  <button
                    onClick={() => removeMod(mod.id)}
                    className="p-2 rounded-md hover:bg-status-error/10 transition-colors"
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

      {/* Add Modal */}
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
              <button
                onClick={() => setShowAddModal(false)}
                className="msm-btn-secondary flex-1 px-4 py-2"
              >
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

      {/* Steam Search Modal */}
      {showSteamSearch && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="font-headline text-headline-md text-primary">{t('mods.steamSearch')}</h2>
                <p className="font-body-md text-sm text-on-surface-variant">{t('mods.steamSearchHint')}</p>
              </div>
              <button
                onClick={() => setShowSteamSearch(false)}
                className="msm-btn-secondary p-2"
              >
                {t('common.close')}
              </button>
            </div>

            {/* Steam Search Input */}
            <div className="flex gap-2 mb-6">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
                <input
                  type="text"
                  placeholder={t('mods.searchPlaceholder')}
                  value={steamQuery}
                  onChange={(e) => setSteamQuery(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && searchSteam()}
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

            {/* Popular Mods */}
            <div>
              <h3 className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-3">
                {t('mods.popularMods')}
              </h3>
              {popularLoading ? (
                <div className="text-center py-8 text-on-surface-variant font-body-md">{t('common.loading')}</div>
              ) : popularMods.length > 0 ? (
                <div className="space-y-3">
                  {popularMods.map(mod => renderSteamModCard(mod))}
                </div>
              ) : (
                <div className="text-center py-8 text-on-surface-variant font-body-md">
                  {t('mods.noSearchResults')}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}