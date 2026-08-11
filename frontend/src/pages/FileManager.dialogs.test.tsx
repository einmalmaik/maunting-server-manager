import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
