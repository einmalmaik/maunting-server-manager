import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { baueZielUrl } from './searchEngines'
import {
  nativeTabErstellen,
  nativeTabAktivieren,
  nativeTabSchliessen,
  nativeTabNavigieren,
  nativeTabZurueck,
  nativeTabVorwaerts,
  nativeTabNeuLaden,
} from './tauriBridge'

export interface Tab {
  id: string
  url: string
  title: string
  favicon?: string
  isLoading: boolean
  canGoBack: boolean
  canGoForward: boolean
  isPinned: boolean
  isIncognito: boolean
}

export interface HistoryEntry {
  id: string
  url: string
  title: string
  timestamp: number
}

export interface Bookmark {
  id: string
  url: string
  title: string
  favicon?: string
  dateAdded: number
}

export interface DownloadItem {
  id: string
  filename: string
  url: string
  size?: number
  progress: number // 0 bis 100
  state: 'downloading' | 'completed' | 'cancelled' | 'failed'
  timestamp: number
}

export type DrawerType =
  | 'none'
  | 'messenger'
  | 'ai'
  | 'vault'
  | 'settings'
  | 'history'
  | 'bookmarks'
  | 'downloads'

interface BrowserState {
  // Tabs
  tabs: Tab[]
  activeTabId: string

  // Verlauf & Lesezeichen
  history: HistoryEntry[]
  bookmarks: Bookmark[]
  downloads: DownloadItem[]

  // Adblock & Schutz
  blockedAdsCount: number
  blockedTrackersCount: number
  adblockEnabled: boolean
  trackerSchutzEnabled: boolean
  forgetOnClose: boolean
  javascriptEnabled: boolean
  cookiesEnabled: boolean

  // Suchmaschine & Ökosystem
  searchEngine: string
  searxngUrl: string | null
  isCoupled: boolean
  backendUrl: string | null
  accessToken: string | null
  pairedUser: {
    username: string
    email?: string
    avatarUrl?: string
  } | null

  // UI & Personalisierung
  theme: 'system' | 'dark' | 'light'
  customWallpaper: string | null
  wallpaperBlur: number
  wallpaperDim: number
  openDrawer: DrawerType
  isMessengerEnabled: boolean
  isAiEnabled: boolean
  isVaultEnabled: boolean

  // Aktionen für Tabs
  createTab: (url?: string, isIncognito?: boolean) => string
  closeTab: (id: string) => void
  activateTab: (id: string) => void
  updateTab: (id: string, partial: Partial<Tab>) => void
  pinTab: (id: string) => void
  navigateActiveTab: (input: string) => void
  reloadActiveTab: () => void
  goBackActiveTab: () => void
  goForwardActiveTab: () => void

  // Lesezeichen & Verlauf
  addBookmark: (url: string, title: string) => void
  removeBookmark: (url: string) => void
  isBookmarked: (url: string) => boolean
  addHistoryEntry: (url: string, title: string) => void
  clearHistory: () => void
  clearBrowserData: (types: { history?: boolean; cookies?: boolean; cache?: boolean }) => void

  // UI-Aktionen
  setOpenDrawer: (drawer: DrawerType) => void
  toggleDrawer: (drawer: DrawerType) => void
  setTheme: (theme: 'system' | 'dark' | 'light') => void
  setSearchEngine: (engine: string) => void
  setSearxngUrl: (url: string | null) => void
  setCustomWallpaper: (wallpaper: string | null) => void
  setWallpaperBlur: (blur: number) => void
  setWallpaperDim: (dim: number) => void
  toggleAdblock: () => void
  incrementBlockCount: (type: 'ad' | 'tracker') => void
  setBlockedStats: (ads: number, trackers: number) => void

  // Kopplung
  setCouplingData: (backendUrl: string, token: string, user: { username: string; email?: string }) => void
  decouple: () => void
}

const INITIAL_TAB_ID = 'tab-initial'

export const useBrowserStore = create<BrowserState>()(
  persist(
    (set, get) => ({
      tabs: [
        {
          id: INITIAL_TAB_ID,
          url: 'about:blank',
          title: 'Neuer Tab',
          isLoading: false,
          canGoBack: false,
          canGoForward: false,
          isPinned: false,
          isIncognito: false,
        },
      ],
      activeTabId: INITIAL_TAB_ID,

      history: [],
      bookmarks: [
        {
          id: 'bm-1',
          url: 'https://ecosia.org',
          title: 'Ecosia Suchmaschine',
          dateAdded: Date.now(),
        },
        {
          id: 'bm-2',
          url: 'https://duckduckgo.com',
          title: 'DuckDuckGo',
          dateAdded: Date.now(),
        },
      ],
      downloads: [],

      blockedAdsCount: 0,
      blockedTrackersCount: 0,
      adblockEnabled: true,
      trackerSchutzEnabled: true,
      forgetOnClose: false,
      javascriptEnabled: true,
      cookiesEnabled: true,

      searchEngine: 'google',
      searxngUrl: null,
      isCoupled: false,
      backendUrl: null,
      accessToken: null,
      pairedUser: null,

      theme: 'system',
      customWallpaper: null,
      wallpaperBlur: 0,
      wallpaperDim: 40,
      openDrawer: 'none',
      isMessengerEnabled: true,
      isAiEnabled: true,
      isVaultEnabled: true,

      createTab: (url = 'about:blank', isIncognito = false) => {
        const id = `tab-${Math.random().toString(36).substring(2, 9)}`
        const title = url === 'about:blank' ? 'Neuer Tab' : url
        const newTab: Tab = {
          id,
          url,
          title,
          isLoading: false,
          canGoBack: false,
          canGoForward: false,
          isPinned: false,
          isIncognito,
        }

        set((state) => ({
          tabs: [...state.tabs, newTab],
          activeTabId: id,
        }))

        // Nativen Tab in Tauri erzeugen
        void nativeTabErstellen(url, isIncognito)
        return id
      },

      closeTab: (id: string) => {
        const { tabs, activeTabId, createTab } = get()
        void nativeTabSchliessen(id)

        if (tabs.length <= 1) {
          // Letzten Tab geschlossen -> neuen leeren Tab öffnen
          const newId = createTab()
          set((state) => ({
            tabs: state.tabs.filter((t) => t.id !== id),
            activeTabId: newId,
          }))
          return
        }

        const remaining = tabs.filter((t) => t.id !== id)
        let newActiveId = activeTabId

        if (activeTabId === id) {
          const index = tabs.findIndex((t) => t.id === id)
          const nextTab = remaining[Math.min(index, remaining.length - 1)]
          newActiveId = nextTab.id
        }

        set({
          tabs: remaining,
          activeTabId: newActiveId,
        })
      },

      activateTab: (id: string) => {
        set({ activeTabId: id })
        void nativeTabAktivieren(id)
      },

      updateTab: (id: string, partial: Partial<Tab>) => {
        set((state) => ({
          tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, ...partial } : tab)),
        }))
      },

      pinTab: (id: string) => {
        set((state) => ({
          tabs: state.tabs.map((tab) =>
            tab.id === id ? { ...tab, isPinned: !tab.isPinned } : tab
          ),
        }))
      },

      navigateActiveTab: (input: string) => {
        const { activeTabId, searchEngine, searxngUrl, addHistoryEntry, tabs } = get()
        const targetUrl = baueZielUrl(input, searchEngine, searxngUrl ?? undefined)
        const currentTab = tabs.find((t) => t.id === activeTabId)
        const isBlank = targetUrl === 'about:blank' || targetUrl.startsWith('about:')

        set((state) => ({
          tabs: state.tabs.map((tab) =>
            tab.id === activeTabId
              ? {
                  ...tab,
                  url: targetUrl,
                  title: isBlank ? 'Neuer Tab' : targetUrl,
                  isLoading: !isBlank,
                  canGoBack: true,
                }
              : tab
          ),
        }))

        // Nativen Webview zur Ziel-URL navigieren
        void nativeTabNavigieren(activeTabId, targetUrl)

        // Sicherheits-Timeout: Ladezustand nach maximal 6 Sekunden beenden, falls keine Events feuern
        if (!isBlank) {
          setTimeout(() => {
            set((state) => ({
              tabs: state.tabs.map((tab) =>
                tab.id === activeTabId && tab.isLoading ? { ...tab, isLoading: false } : tab
              ),
            }))
          }, 6000)
        }

        if (!currentTab?.isIncognito && !isBlank) {
          addHistoryEntry(targetUrl, targetUrl)
        }
      },

      reloadActiveTab: () => {
        const { activeTabId } = get()
        void nativeTabNeuLaden(activeTabId)
        set((state) => ({
          tabs: state.tabs.map((tab) =>
            tab.id === activeTabId ? { ...tab, isLoading: true } : tab
          ),
        }))
        setTimeout(() => {
          set((state) => ({
            tabs: state.tabs.map((tab) =>
              tab.id === activeTabId ? { ...tab, isLoading: false } : tab
            ),
          }))
        }, 600)
      },

      goBackActiveTab: () => {
        const { activeTabId } = get()
        void nativeTabZurueck(activeTabId)
      },

      goForwardActiveTab: () => {
        const { activeTabId } = get()
        void nativeTabVorwaerts(activeTabId)
      },

      addBookmark: (url: string, title: string) => {
        const { bookmarks } = get()
        if (bookmarks.some((b) => b.url === url)) return

        const newBookmark: Bookmark = {
          id: `bm-${Date.now()}`,
          url,
          title: title || url,
          dateAdded: Date.now(),
        }
        set({ bookmarks: [newBookmark, ...bookmarks] })
      },

      removeBookmark: (url: string) => {
        set((state) => ({
          bookmarks: state.bookmarks.filter((b) => b.url !== url),
        }))
      },

      isBookmarked: (url: string) => {
        return get().bookmarks.some((b) => b.url === url)
      },

      addHistoryEntry: (url: string, title: string) => {
        const entry: HistoryEntry = {
          id: `h-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
          url,
          title: title || url,
          timestamp: Date.now(),
        }
        set((state) => ({
          history: [entry, ...state.history.slice(0, 499)], // max 500 Einträge
        }))
      },

      clearHistory: () => {
        set({ history: [] })
      },

      clearBrowserData: (types) => {
        set((state) => ({
          history: types.history ? [] : state.history,
          downloads: types.history ? [] : state.downloads,
        }))
      },

      setOpenDrawer: (drawer: DrawerType) => {
        set({ openDrawer: drawer })
      },

      toggleDrawer: (drawer: DrawerType) => {
        const current = get().openDrawer
        set({ openDrawer: current === drawer ? 'none' : drawer })
      },

      setTheme: (theme) => set({ theme }),
      setSearchEngine: (searchEngine) => set({ searchEngine }),
      setSearxngUrl: (searxngUrl) => set({ searxngUrl }),
      setCustomWallpaper: (customWallpaper) => set({ customWallpaper }),
      setWallpaperBlur: (wallpaperBlur) => set({ wallpaperBlur }),
      setWallpaperDim: (wallpaperDim) => set({ wallpaperDim }),

      toggleAdblock: () => {
        set((state) => ({ adblockEnabled: !state.adblockEnabled }))
      },

      incrementBlockCount: (type) => {
        if (type === 'ad') {
          set((state) => ({ blockedAdsCount: state.blockedAdsCount + 1 }))
        } else {
          set((state) => ({ blockedTrackersCount: state.blockedTrackersCount + 1 }))
        }
      },

      setBlockedStats: (ads: number, trackers: number) => {
        set({ blockedAdsCount: ads, blockedTrackersCount: trackers })
      },

      setCouplingData: (backendUrl, token, user) => {
        set({
          isCoupled: true,
          backendUrl,
          accessToken: token,
          pairedUser: user,
          isVaultEnabled: true,
          isAiEnabled: true,
          isMessengerEnabled: true,
        })
      },

      decouple: () => {
        set({
          isCoupled: false,
          backendUrl: null,
          accessToken: null,
          pairedUser: null,
          openDrawer: 'none',
        })
      },
    }),
    {
      name: 'msb-browser-state',
      partialize: (state) => ({
        history: state.history,
        bookmarks: state.bookmarks,
        searchEngine: state.searchEngine,
        searxngUrl: state.searxngUrl,
        theme: state.theme,
        customWallpaper: state.customWallpaper,
        wallpaperBlur: state.wallpaperBlur,
        wallpaperDim: state.wallpaperDim,
        adblockEnabled: state.adblockEnabled,
        trackerSchutzEnabled: state.trackerSchutzEnabled,
        forgetOnClose: state.forgetOnClose,
        isCoupled: state.isCoupled,
        backendUrl: state.backendUrl,
        accessToken: state.accessToken,
        pairedUser: state.pairedUser,
        isMessengerEnabled: state.isMessengerEnabled,
        isAiEnabled: state.isAiEnabled,
        isVaultEnabled: state.isVaultEnabled,
      }),
    }
  )
)
