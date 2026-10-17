import { beforeEach, describe, expect, it, vi } from 'vitest'

const { verworfenIds } = vi.hoisted(() => ({ verworfenIds: [] as string[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    if (befehl === 'tab_verwerfen' && typeof args?.id === 'string') {
      verworfenIds.push(args.id)
    }
    return Promise.resolve(null)
  },
}))

import { STANDARD_WORKSPACE_ID, useWorkspacesStore } from './workspacesStore'
import { useTabsStore } from './tabsStore'
import { nativ } from './nativ'

describe('useWorkspacesStore', () => {
  beforeEach(() => {
    verworfenIds.length = 0
    vi.spyOn(nativ, 'tabVerwerfen').mockImplementation(async (id: string) => {
      verworfenIds.push(id)
      return null
    })
    useTabsStore.setState({
      tabs: [
        {
          id: 'tab-1',
          url: 'https://example.com',
          titel: 'Example',
          favicon: null,
          laedt: false,
          zurueck: false,
          vor: false,
          privat: false,
          werbung: 0,
          tracker: 0,
          abgestuerzt: false,
          fehler: null,
          gesperrt: null,
          status: '',
          treffer: null,
          nativDa: true,
          ton: false,
          stumm: false,
          ruhe: null,
          vorher: null,
          elternId: null,
          baumEingeklappt: false,
          workspaceId: STANDARD_WORKSPACE_ID,
        },
      ],
      aktivId: 'tab-1',
      verlaufIds: [],
      geschlossen: [],
    })

    useWorkspacesStore.setState({
      workspaces: [
        {
          id: STANDARD_WORKSPACE_ID,
          name: 'Alltag',
          icon: 'globe',
          farbe: 'sky',
          erstelltAm: 0,
        },
      ],
      aktiverWorkspaceId: STANDARD_WORKSPACE_ID,
      letzterTabJeWorkspace: {},
    })
  })

  it('initialisiert mit Standard-Workspace "Alltag"', () => {
    const zustand = useWorkspacesStore.getState()
    expect(zustand.aktiverWorkspaceId).toBe(STANDARD_WORKSPACE_ID)
    expect(zustand.workspaces).toHaveLength(1)
    expect(zustand.workspaces[0].name).toBe('Alltag')
  })

  it('erstellt neuen Workspace und wechselt automatisch dorthin', () => {
    const wsId = useWorkspacesStore.getState().neuerWorkspace('Entwicklung', 'terminal', 'emerald')
    const zustand = useWorkspacesStore.getState()

    expect(zustand.workspaces).toHaveLength(2)
    expect(zustand.aktiverWorkspaceId).toBe(wsId)
    const neu = zustand.workspaces.find((w) => w.id === wsId)
    expect(neu?.name).toBe('Entwicklung')
    expect(neu?.icon).toBe('terminal')
    expect(neu?.farbe).toBe('emerald')

    // Sollte einen neuen Start-Tab für den leeren Workspace angelegt haben
    const tabs = useTabsStore.getState().tabs
    const wsTabs = tabs.filter((t) => t.workspaceId === wsId)
    expect(wsTabs.length).toBeGreaterThanOrEqual(1)
  })

  it('bearbeitet Name, Icon und Farbe eines bestehenden Workspace', () => {
    useWorkspacesStore.getState().bearbeitenWorkspace(STANDARD_WORKSPACE_ID, {
      name: 'Hauptbereich',
      icon: 'briefcase',
      farbe: 'amber',
    })

    const standard = useWorkspacesStore.getState().workspaces.find((w) => w.id === STANDARD_WORKSPACE_ID)
    expect(standard?.name).toBe('Hauptbereich')
    expect(standard?.icon).toBe('briefcase')
    expect(standard?.farbe).toBe('amber')
  })

  it('schickt Tabs des verlassenen Workspace in Deep-Sleep (tabVerwerfen)', async () => {
    const wsId = useWorkspacesStore.getState().neuerWorkspace('Gaming', 'gamepad', 'violet')

    // Beim Wechsel von Standard zu Gaming sollte tab-1 verworfen worden sein
    expect(verworfenIds).toContain('tab-1')

    const tab1 = useTabsStore.getState().tabs.find((t) => t.id === 'tab-1')
    expect(tab1?.nativDa).toBe(false)
    expect(tab1?.ruhe).toBe('verworfen')
    expect(useWorkspacesStore.getState().aktiverWorkspaceId).toBe(wsId)
  })

  it('wechselt per Index (Alt+1..9)', () => {
    const ws2 = useWorkspacesStore.getState().neuerWorkspace('Projekt B')
    expect(useWorkspacesStore.getState().aktiverWorkspaceId).toBe(ws2)

    // Index 0 -> Standard
    useWorkspacesStore.getState().wechselnNachIndex(0)
    expect(useWorkspacesStore.getState().aktiverWorkspaceId).toBe(STANDARD_WORKSPACE_ID)

    // Index 1 -> Projekt B
    useWorkspacesStore.getState().wechselnNachIndex(1)
    expect(useWorkspacesStore.getState().aktiverWorkspaceId).toBe(ws2)

    // Ungültiger Index 99 -> keine Änderung
    useWorkspacesStore.getState().wechselnNachIndex(99)
    expect(useWorkspacesStore.getState().aktiverWorkspaceId).toBe(ws2)
  })

  it('löscht Workspace und verschiebt dessen Tabs in Standard-Workspace', () => {
    const wsId = useWorkspacesStore.getState().neuerWorkspace('Temp')
    const tabsStore = useTabsStore.getState()
    const tempTab = tabsStore.tabs.find((t) => t.workspaceId === wsId)
    expect(tempTab).toBeDefined()

    useWorkspacesStore.getState().loeschenWorkspace(wsId)

    const zustand = useWorkspacesStore.getState()
    expect(zustand.workspaces.some((w) => w.id === wsId)).toBe(false)
    expect(zustand.aktiverWorkspaceId).toBe(STANDARD_WORKSPACE_ID)

    // Der Tab wurde nach Standard verschoben
    const verschobenerTab = useTabsStore.getState().tabs.find((t) => t.id === tempTab!.id)
    expect(verschobenerTab?.workspaceId).toBe(STANDARD_WORKSPACE_ID)
  })

  it('verhindert das Löschen des Standard-Workspace', () => {
    useWorkspacesStore.getState().loeschenWorkspace(STANDARD_WORKSPACE_ID)
    const zustand = useWorkspacesStore.getState()
    expect(zustand.workspaces.some((w) => w.id === STANDARD_WORKSPACE_ID)).toBe(true)
  })

  it('verschiebt Tab in anderen Workspace über verschiebeTabInWorkspace', () => {
    const wsId = useWorkspacesStore.getState().neuerWorkspace('Arbeit')
    // Wechsle zurück zu Standard
    useWorkspacesStore.getState().wechselnWorkspace(STANDARD_WORKSPACE_ID)

    // Verschiebe tab-1 in den Arbeitsbereich
    useTabsStore.getState().verschiebeTabInWorkspace('tab-1', wsId)

    const tab1 = useTabsStore.getState().tabs.find((t) => t.id === 'tab-1')
    expect(tab1?.workspaceId).toBe(wsId)
  })
})
