import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FileManager } from './FileManager'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'
import type { BrowseResponse } from '@/components/server/fileWorkspaceTypes'

// Der Editor bringt CodeMirror mit und hat mit den Dialogen nichts zu tun — er
// wuerde den Test nur langsam und stoeranfaellig machen.
vi.mock('@/components/server/FileEditorWorkspace', () => ({
  FileEditorWorkspace: () => null,
}))

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  SanitizedApiError: class SanitizedApiError extends Error {
    constructor(message: string) {
      super(message)
      this.name = 'SanitizedApiError'
    }
  },
}))

const mockApi = vi.mocked(client.api)

const SERVER_ID = 4242

// Synthetisches Listing, keine echten Serverdaten.
const ROOT_LISTING: BrowseResponse = {
  path: '',
  exists: true,
  entries: [
    { name: 'server.properties', is_dir: false, size: 128, modified: 0, mode: null, owner: null, group: null },
  ],
}

const OWNER: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

/** Rendert den Dateimanager, oeffnet das Kontextmenue auf der Beispieldatei und
 * waehlt den Eintrag mit der angegebenen Beschriftung. */
async function chooseFromContextMenu(label: string) {
  render(<FileManager serverId={SERVER_ID} />)
  fireEvent.contextMenu(await screen.findByText('server.properties'))
  fireEvent.click(await screen.findByRole('menuitem', { name: label }))
}

describe('FileManager: Verschieben- und Umbenennen-Dialog', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    mockApi.mockImplementation(async () => ROOT_LISTING as any)
    await i18n.changeLanguage('en')
    usePermissionsStore.setState({ me: OWNER, isLoading: false, error: null })
  })

  it('schliesst den Verschieben-Dialog mit Escape, auch wenn der Fokus nicht im Eingabefeld steht', async () => {
    await chooseFromContextMenu(i18n.t('files.move'))
    expect(await screen.findByText(i18n.t('files.moveTargetHint'))).toBeInTheDocument()

    // Bewusst auf dem <body>: genau so kommt die Taste an, wenn der Benutzer
    // nicht im Eingabefeld steht. Ein Handler am Feld feuert hier nie.
    fireEvent.keyDown(document.body, { key: 'Escape' })

    await waitFor(() => {
      expect(screen.queryByText(i18n.t('files.moveTargetHint'))).not.toBeInTheDocument()
    })
  })

  it('meldet den Verschieben-Dialog als Dialog und schliesst ihn nur beim Klick daneben', async () => {
    await chooseFromContextMenu(i18n.t('files.move'))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('files.move') })
    expect(dialog).toHaveAttribute('aria-modal', 'true')

    // Ein Klick INNERHALB der Karte darf nichts schliessen ...
    fireEvent.click(screen.getByText(i18n.t('files.moveTargetHint')))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    // ... ein Klick auf die abgedunkelte Flaeche daneben schon.
    fireEvent.click(dialog)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('schliesst den Umbenennen-Dialog mit Escape vom Dokument aus', async () => {
    await chooseFromContextMenu(i18n.t('files.rename'))
    expect(await screen.findByRole('dialog', { name: i18n.t('files.renameTitle') })).toBeInTheDocument()

    fireEvent.keyDown(document.body, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

describe('FileManager: Bausteine und Telefon', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    mockApi.mockImplementation(async () => ROOT_LISTING as any)
    await i18n.changeLanguage('en')
    usePermissionsStore.setState({ me: OWNER, isLoading: false, error: null })
  })

  it('bindet die Eingabefelder der Dialoge an ihre Beschriftung (Input statt nativem Feld)', async () => {
    await chooseFromContextMenu(i18n.t('files.rename'))
    expect(await screen.findByLabelText(i18n.t('files.newName'))).toHaveValue('server.properties')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    fireEvent.contextMenu(screen.getByText('server.properties'))
    fireEvent.click(await screen.findByRole('menuitem', { name: i18n.t('files.move') }))
    expect(await screen.findByLabelText(i18n.t('files.targetFolder'))).toHaveFocus()
  })

  it('schließt den Umbenennen-Dialog mit der Zurück-Taste', async () => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    await chooseFromContextMenu(i18n.t('files.rename'))
    await screen.findByRole('dialog', { name: i18n.t('files.renameTitle') })
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('zeigt in einem Unterordner den Knopf „eine Ebene hoch“', async () => {
    const mitOrdner: BrowseResponse = {
      path: '',
      exists: true,
      entries: [{ name: 'mods', is_dir: true, size: 0, modified: 0, mode: null, owner: null, group: null }],
    }
    mockApi.mockImplementation(async (pfad: string) => (pfad.includes('path=mods') ? { path: 'mods', exists: true, entries: [] } : mitOrdner) as any)
    render(<FileManager serverId={SERVER_ID} />)
    expect(screen.queryByRole('button', { name: i18n.t('files.parentFolder') })).toBeNull()

    fireEvent.click(await screen.findByText('mods'))
    const hoch = await screen.findByRole('button', { name: i18n.t('files.parentFolder') })
    fireEvent.click(hoch)
    await waitFor(() => expect(screen.queryByRole('button', { name: i18n.t('files.parentFolder') })).toBeNull())
  })
})

describe('FileManager: Dateisuche', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    await i18n.changeLanguage('en')
    usePermissionsStore.setState({ me: OWNER, isLoading: false, error: null })
  })

  it('zeigt die Treffer der zuletzt getippten Suche, auch wenn die ältere später zurückkommt', async () => {
    // Die Inhaltssuche liest Dateien auf dem Zielserver; wie lange sie braucht,
    // hängt am Datenbestand. Die ältere, breitere Suche kann deshalb nach der
    // neueren zurückkommen — und darf deren Treffer nicht überschreiben.
    const antworten = new Map<string, (wert: unknown) => void>()
    mockApi.mockImplementation(
      (pfad: string) =>
        new Promise((resolve) => {
          if (!pfad.includes('/search')) {
            resolve(ROOT_LISTING as any)
            return
          }
          antworten.set(pfad.includes('ser&') || pfad.endsWith('q=ser') ? 'alt' : 'neu', resolve)
        }) as any,
    )

    vi.useFakeTimers()
    try {
      render(<FileManager serverId={SERVER_ID} />)
      const feld = screen.getByPlaceholderText(i18n.t('files.searchPlaceholder'))

      fireEvent.change(feld, { target: { value: 'ser' } })
      await act(async () => {
        vi.advanceTimersByTime(400)
      })
      fireEvent.change(feld, { target: { value: 'server' } })
      await act(async () => {
        vi.advanceTimersByTime(400)
      })
      expect(antworten.has('alt')).toBe(true)
      expect(antworten.has('neu')).toBe(true)

      // Die neuere Suche kommt zuerst zurück, die ältere danach.
      await act(async () => {
        antworten.get('neu')!({ truncated: false, results: [{ path: 'server.cfg', is_dir: false }] })
      })
      await act(async () => {
        antworten.get('alt')!({ truncated: false, results: [{ path: 'veraltet.txt', is_dir: false }] })
      })

      expect(screen.getByText('server.cfg')).toBeInTheDocument()
      expect(screen.queryByText('veraltet.txt')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
