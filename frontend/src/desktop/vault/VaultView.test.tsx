/**
 * Tresor-Ansichten: Tresor, Archiv, Papierkorb (09/2026).
 *
 * Löschen legt in den Papierkorb und fragt nicht, weil es sich zurücknehmen
 * lässt. Endgültig gelöscht wird nur im Papierkorb, nach Rückfrage. Einträge
 * einer Art, die diese App nicht kennt, erscheinen nirgends.
 */

import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { VaultView, restTageImPapierkorb } from './VaultView'
import { useVaultStore, type VaultItem } from './vaultStore'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn().mockResolvedValue(undefined),
  biometrieEntsperren: vi.fn().mockResolvedValue(''),
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn().mockResolvedValue(undefined),
}))

const TAG = 24 * 60 * 60 * 1000

function eintrag(id: string, service: string, extra: Partial<VaultItem> = {}): VaultItem {
  return { id, service, username: 'ich', password: 'pw', category: 'login', createdAt: 1, updatedAt: 1, revision: 1, ...extra }
}

describe('VaultView: Archiv und Papierkorb', () => {
  const trashItem = vi.fn(async () => undefined)
  const restoreItem = vi.fn(async () => undefined)
  const deleteItem = vi.fn(async () => undefined)

  beforeEach(() => {
    trashItem.mockClear()
    restoreItem.mockClear()
    deleteItem.mockClear()
    useVaultStore.setState({
      isInitialized: true,
      isUnlocked: true,
      hasHint: true,
      searchQuery: '',
      syncWithServer: vi.fn(async () => undefined),
      checkHintStatus: vi.fn(async () => true),
      trashItem,
      restoreItem,
      deleteItem,
      items: [
        eintrag('a', 'Bank'),
        eintrag('b', 'Altes Forum', { archivedAt: 5 }),
        eintrag('c', 'Weg damit', { trashedAt: Date.now() - 3 * TAG }),
        eintrag('d', 'urlaub.jpg', { category: 'datei' }),
      ],
    })
  })

  const reiter = (schluessel: string) => screen.getByRole('tab', { name: new RegExp(i18n.t(schluessel)) })

  it('zeigt im Tresor nur, was weder archiviert noch gelöscht ist, und nichts Unbekanntes', () => {
    render(<VaultView />)
    expect(screen.getByText('Bank')).toBeInTheDocument()
    expect(screen.queryByText('Altes Forum')).not.toBeInTheDocument()
    expect(screen.queryByText('Weg damit')).not.toBeInTheDocument()
    expect(screen.queryByText('urlaub.jpg')).not.toBeInTheDocument()
  })

  it('zeigt das Archiv für sich', () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.archiv'))
    expect(screen.getByText('Altes Forum')).toBeInTheDocument()
    expect(screen.queryByText('Bank')).not.toBeInTheDocument()
  })

  it('zeigt im Papierkorb die Restfrist und stellt wieder her', () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.papierkorb'))
    const zeile = screen.getByText('Weg damit').closest('div.group') as HTMLElement
    expect(within(zeile).getByText(i18n.t('mss.vault.nochTage', { count: 27 }))).toBeInTheDocument()

    fireEvent.click(within(zeile).getByRole('button', { name: new RegExp(i18n.t('mss.vault.wiederherstellen')) }))
    expect(restoreItem).toHaveBeenCalledWith('c')
    expect(deleteItem).not.toHaveBeenCalled()
  })

  it('legt aus dem Bearbeiten-Dialog in den Papierkorb, ohne endgültig zu löschen', async () => {
    render(<VaultView />)
    const zeile = screen.getByText('Bank').closest('div.group') as HTMLElement
    const knoepfe = within(zeile).getAllByRole('button')
    fireEvent.click(knoepfe[knoepfe.length - 1])

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.inPapierkorb')) }))
    await vi.waitFor(() => expect(trashItem).toHaveBeenCalledWith('a'))
    expect(deleteItem).not.toHaveBeenCalled()
  })

  it('rechnet die Restfrist in ganzen Tagen und nie unter null', () => {
    const jetzt = 1_000 * TAG
    expect(restTageImPapierkorb(jetzt, jetzt)).toBe(30)
    expect(restTageImPapierkorb(jetzt - 29.5 * TAG, jetzt)).toBe(1)
    expect(restTageImPapierkorb(jetzt - 40 * TAG, jetzt)).toBe(0)
  })
})
