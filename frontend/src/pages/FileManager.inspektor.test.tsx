import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { FileManager } from './FileManager'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { BrowseResponse, ReadResponse } from '@/components/server/fileWorkspaceTypes'

vi.mock('@/components/server/FileEditorWorkspace', () => ({
  FileEditorWorkspace: () => null,
}))

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  SanitizedApiError: class SanitizedApiError extends Error {},
}))

const mockApi = vi.mocked(client.api)

const LISTE: BrowseResponse = {
  path: '',
  exists: true,
  entries: [{ name: 'eula.txt', is_dir: false, size: 0, modified: 0, mode: null, owner: null, group: null }],
}

const DATEI: ReadResponse = {
  path: 'eula.txt',
  name: 'eula.txt',
  content: 'eula=false',
  revision: 'r1',
  size: 10,
  modified: 0,
  mode: null,
  owner: null,
  group: null,
}

describe('FileManager: Details der offenen Datei', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    mockApi.mockImplementation(async (pfad: string) => (pfad.includes('/read?') ? DATEI : pfad.includes('/versions') ? { versions: [] } : LISTE) as never)
    await i18n.changeLanguage('en')
    usePermissionsStore.setState({
      me: { is_owner: true, role_id: null, role_name: null, global_keys: [], server_keys: {} },
      isLoading: false,
      error: null,
    })
  })

  it('nennt eine unbekannte Änderungszeit in der Sprache der App', async () => {
    // Bis 02.10.2026 stand hier fest „Nicht verfügbar“, auch in der englischen App.
    render(<FileManager serverId={7} />)
    fireEvent.click(await screen.findByText('eula.txt'))
    const geaendert = (await screen.findByText(i18n.t('files.modified'))).nextElementSibling
    expect(geaendert).toHaveTextContent(i18n.t('files.notAvailable'))
    expect(screen.queryByText('Nicht verfügbar')).not.toBeInTheDocument()
  })
})
