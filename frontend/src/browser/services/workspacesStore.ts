/**
 * Lokales Workspace-System (Arbeitsbereiche) für Maunting Pontis.
 *
 * Verwaltet getrennte Arbeitsbereiche (z. B. Alltag, Dev, Gaming).
 * Jeder Workspace hat eigene Tabs und ein isoliertes WebView2-Profil
 * (separate Cookies, LocalStorage, Sitzungen).
 *
 * Beim Umschalten werden Tabs des verlassenen Bereichs in den nativen
 * Deep-Sleep (0 MB RAM) geschickt.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ablage } from './ablage'
import { nativ } from './nativ'
import { useTabsStore } from './tabsStore'

export interface Workspace {
  id: string
  name: string
  icon: string
  farbe: string
  erstelltAm: number
}

export interface WorkspacesZustand {
  workspaces: Workspace[]
  aktiverWorkspaceId: string
  letzterTabJeWorkspace: Record<string, string>

  neuerWorkspace: (name: string, icon?: string, farbe?: string) => string
  bearbeitenWorkspace: (id: string, daten: Partial<Pick<Workspace, 'name' | 'icon' | 'farbe'>>) => void
  loeschenWorkspace: (id: string) => void
  wechselnWorkspace: (id: string) => void
  wechselnNachIndex: (index: number) => void
}

export const STANDARD_WORKSPACE_ID = 'standard'

export const STANDARD_WORKSPACES: Workspace[] = [
  {
    id: STANDARD_WORKSPACE_ID,
    name: 'Alltag',
    icon: 'globe',
    farbe: 'primary',
    erstelltAm: 0,
  },
]

function saubereWorkspaceId(name: string): string {
  const bereinigt = name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9_-]/g, '')
    .slice(0, 16)
  const zufall = Math.random().toString(36).slice(2, 6)
  return bereinigt ? `ws-${bereinigt}-${zufall}` : `ws-${zufall}`
}

export const useWorkspacesStore = create<WorkspacesZustand>()(
  persist(
    (set, get) => ({
      workspaces: STANDARD_WORKSPACES,
      aktiverWorkspaceId: STANDARD_WORKSPACE_ID,
      letzterTabJeWorkspace: {},

      neuerWorkspace: (name: string, icon = 'folder', farbe = 'emerald') => {
        const id = saubereWorkspaceId(name)
        const neuer: Workspace = {
          id,
          name: name.trim() || 'Arbeitsbereich',
          icon,
          farbe,
          erstelltAm: Date.now(),
        }
        set((s) => ({
          workspaces: [...s.workspaces, neuer],
        }))
        get().wechselnWorkspace(id)
        return id
      },

      bearbeitenWorkspace: (id: string, daten) => {
        set((s) => ({
          workspaces: s.workspaces.map((w) => (w.id === id ? { ...w, ...daten } : w)),
        }))
      },

      loeschenWorkspace: (id: string) => {
        if (id === STANDARD_WORKSPACE_ID || get().workspaces.length <= 1) return

        const uebrige = get().workspaces.filter((w) => w.id !== id)
        const zielId = uebrige[0]?.id ?? STANDARD_WORKSPACE_ID

        // Tabs des gelöschten Bereichs in den Ziel-Workspace verschieben
        const tabsStore = useTabsStore.getState()
        const betroffeneTabs = tabsStore.tabs.filter((t) => (t.workspaceId ?? STANDARD_WORKSPACE_ID) === id)
        for (const tab of betroffeneTabs) {
          tabsStore.verschiebeTabInWorkspace(tab.id, zielId)
        }

        const vorherAktiv = get().aktiverWorkspaceId
        set((s) => {
          const gemerkt = { ...s.letzterTabJeWorkspace }
          delete gemerkt[id]
          return {
            workspaces: uebrige,
            letzterTabJeWorkspace: gemerkt,
          }
        })

        if (vorherAktiv === id) {
          get().wechselnWorkspace(zielId)
        }
      },

      wechselnWorkspace: (zielId: string) => {
        const { workspaces, aktiverWorkspaceId, letzterTabJeWorkspace } = get()
        if (zielId === aktiverWorkspaceId) return
        if (!workspaces.some((w) => w.id === zielId)) return

        const tabsStore = useTabsStore.getState()
        const alterTabId = tabsStore.aktivId

        // Merke den letzten aktiven Tab des bisherigen Workspace
        const aktualisiertLetzter = {
          ...letzterTabJeWorkspace,
          [aktiverWorkspaceId]: alterTabId,
        }

        // Bisherigen Workspace in Deep-Sleep schicken:
        // Alle Tabs ohne aktiven Ton entladen, um 0 MB RAM zu verbrauchen
        const verlasseneTabs = tabsStore.tabs.filter(
          (t) => (t.workspaceId ?? STANDARD_WORKSPACE_ID) === aktiverWorkspaceId
        )
        for (const tab of verlasseneTabs) {
          if (tab.nativDa && !tab.ton) {
            void nativ.tabVerwerfen(tab.id)
            useTabsStore.setState((s) => ({
              tabs: s.tabs.map((t) => (t.id === tab.id ? { ...t, nativDa: false, ruhe: 'verworfen' } : t)),
            }))
          }
        }

        set({
          aktiverWorkspaceId: zielId,
          letzterTabJeWorkspace: aktualisiertLetzter,
        })

        // Passenden Tab im neuen Workspace finden und aktivieren
        const aktuelleTabs = useTabsStore.getState().tabs
        const zielTabs = aktuelleTabs.filter((t) => (t.workspaceId ?? STANDARD_WORKSPACE_ID) === zielId)

        const gemerkterTabId = aktualisiertLetzter[zielId]
        const gefundener = zielTabs.find((t) => t.id === gemerkterTabId) ?? zielTabs[0]

        if (gefundener) {
          tabsStore.aktivieren(gefundener.id)
          // Falls der Tab verworfen war und eine Adresse hat, aufwecken
          if (gefundener.ruhe === 'verworfen' && gefundener.url) {
            tabsStore.oeffnen(gefundener.url, gefundener.id)
          }
        } else {
          // Neuer Workspace ohne Tabs: Startseiten-Tab anlegen
          tabsStore.neuerTab('', { workspaceId: zielId })
        }
      },

      wechselnNachIndex: (index: number) => {
        const { workspaces } = get()
        if (index >= 0 && index < workspaces.length) {
          get().wechselnWorkspace(workspaces[index].id)
        }
      },
    }),
    {
      name: 'msb:workspaces',
      version: 1,
      storage: ablage,
      partialize: (s) => ({
        workspaces: s.workspaces,
        aktiverWorkspaceId: s.aktiverWorkspaceId,
        letzterTabJeWorkspace: s.letzterTabJeWorkspace,
      }),
    }
  )
)
