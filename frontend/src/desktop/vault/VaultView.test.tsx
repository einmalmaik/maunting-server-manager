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
import { usePromptStore } from '@/stores/promptStore'

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

vi.mock('./tresorBlobApi', () => ({
  speicherAbfragen: vi.fn().mockResolvedValue({ belegt: 1024 * 1024, quote: 10 * 1024 * 1024 * 1024, in_loeschung: 0 }),
}))

vi.mock('./tresorMiniaturen', () => ({
  useMiniatur: () => null,
  miniaturenVorladen: vi.fn(async () => undefined),
}))

const geladen = vi.hoisted(() => ({ blobs: [] as string[] }))
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  blobLesen: vi.fn(async (kopf: { id: string }) => {
    geladen.blobs.push(kopf.id)
    return new Blob(['bild'], { type: 'image/webp' })
  }),
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

describe('VaultView: Dateien', () => {
  const ordnerAnlegen = vi.fn(async () => 'neu')

  beforeEach(() => {
    ordnerAnlegen.mockClear()
    useVaultStore.setState({
      isInitialized: true,
      isUnlocked: true,
      hasHint: true,
      searchQuery: '',
      syncWithServer: vi.fn(async () => undefined),
      checkHintStatus: vi.fn(async () => true),
      ordnerAnlegen,
      items: [
        eintrag('a', 'Bank'),
        eintrag('o1', 'Urlaub', { category: 'ordner', username: '', password: '' }),
        eintrag('f1', 'strand.jpg', { category: 'datei', ordner: 'o1', username: '', password: '' }),
        eintrag('f2', 'vertrag.pdf', { category: 'datei', username: '', password: '' }),
        eintrag('f3', 'weg.txt', { category: 'datei', trashedAt: Date.now(), username: '', password: '' }),
      ],
    })
  })

  const reiter = (schluessel: string) => screen.getByRole('tab', { name: new RegExp(i18n.t(schluessel)) })

  it('zeigt Ordner und Dateien der Ebene, nichts aus dem Papierkorb, und keine Passwörter', async () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.dateien'))

    expect(screen.getByText('Urlaub')).toBeInTheDocument()
    expect(screen.getByText('vertrag.pdf')).toBeInTheDocument()
    expect(screen.queryByText('strand.jpg')).not.toBeInTheDocument()
    expect(screen.queryByText('weg.txt')).not.toBeInTheDocument()
    expect(screen.queryByText('Bank')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Urlaub'))
    expect(screen.getByText('strand.jpg')).toBeInTheDocument()
    expect(screen.queryByText('vertrag.pdf')).not.toBeInTheDocument()
  })

  it('legt einen Ordner in der geöffneten Ebene an', async () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.dateien'))
    fireEvent.click(screen.getByText('Urlaub'))
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.dateien.ordnerAnlegen')) }))

    await vi.waitFor(() => expect(usePromptStore.getState().pending).not.toBeNull())
    usePromptStore.getState().resolve('Strandbilder')
    await vi.waitFor(() => expect(ordnerAnlegen).toHaveBeenCalledWith('Strandbilder', 'o1'))
  })

  it('führt Dateien im Papierkorb mit den Passwörtern zusammen', () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.papierkorb'))
    expect(screen.getByText('weg.txt')).toBeInTheDocument()
  })
})

describe('VaultView: Fotos', () => {
  const kopf = (id: string, echt = 100) => ({ id: id.padEnd(32, '0'), groesse: 65536, echt, schluessel: 'x', loeschen: 'y' })
  const medium = (id: string, service: string, typ: string, extra: Partial<VaultItem> = {}, angaben = {}): VaultItem =>
    eintrag(id, service, {
      category: 'datei',
      username: '',
      password: '',
      datei: { typ, original: kopf(`${id}a`, 5000), vorschau: kopf(`${id}b`), miniatur: kopf(`${id}c`), ...angaben },
      ...extra,
    })

  const albumAnlegen = vi.fn(async () => 'neues-album')
  const albumAendern = vi.fn(async () => undefined)

  beforeEach(() => {
    geladen.blobs = []
    albumAnlegen.mockClear()
    albumAendern.mockClear()
    useVaultStore.setState({
      albumAnlegen,
      albumAendern,
      isInitialized: true,
      isUnlocked: true,
      hasHint: true,
      searchQuery: '',
      userKey: {} as CryptoKey,
      syncWithServer: vi.fn(async () => undefined),
      checkHintStatus: vi.fn(async () => true),
      items: [
        eintrag('a', 'Bank'),
        medium('1', 'strand.jpg', 'image/jpeg', {}, { aufgenommen: Date.UTC(2026, 6, 14, 18, 0), kamera: 'Google Pixel 8' }),
        medium('2', 'wellen.mp4', 'video/mp4', {}, { geaendert: new Date(2026, 5, 3).getTime(), dauer: 75 }),
        medium('3', 'Screenshot_20260712.png', 'image/png', {}, { aufgenommen: Date.UTC(2026, 6, 12) }),
        medium('4', 'geloescht.jpg', 'image/jpeg', { trashedAt: Date.now() }),
        medium('5', 'vertrag.pdf', 'application/pdf'),
        eintrag('al', 'Sommer', { category: 'album', username: '', password: '', album: { eintraege: ['1', '4', '3', 'weg'] } }),
      ],
    })
  })

  const reiter = (schluessel: string) => screen.getByRole('tab', { name: new RegExp(i18n.t(schluessel)) })
  const kachel = (name: string) => screen.queryByRole('button', { name })

  it('zeigt Bilder und Videos nach Monaten, ohne Papierkorb, PDFs und Passwörter', () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.fotos'))

    const juli = screen.getByRole('region', { name: 'Juli 2026' })
    const juni = screen.getByRole('region', { name: 'Juni 2026' })
    expect(within(juli).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'strand.jpg',
      'Screenshot_20260712.png',
    ])
    expect(within(juni).getByRole('button', { name: 'wellen.mp4' })).toHaveTextContent('1:15')
    expect(kachel('geloescht.jpg')).toBeNull()
    expect(kachel('vertrag.pdf')).toBeNull()
    expect(screen.queryByText('Bank')).toBeNull()
  })

  it('filtert nach Videos, Screenshots und Suchwort', () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.fotos'))

    fireEvent.click(reiter('mss.vault.fotos.videos'))
    expect(kachel('wellen.mp4')).not.toBeNull()
    expect(kachel('strand.jpg')).toBeNull()

    fireEvent.click(reiter('mss.vault.fotos.screenshots'))
    expect(kachel('Screenshot_20260712.png')).not.toBeNull()
    expect(kachel('strand.jpg')).toBeNull()

    fireEvent.click(reiter('mss.vault.fotos.alle'))
    fireEvent.change(screen.getByPlaceholderText(i18n.t('common.search')), { target: { value: 'pixel' } })
    expect(kachel('strand.jpg')).not.toBeNull()
    expect(kachel('wellen.mp4')).toBeNull()
  })

  it('öffnet die Lichtbox mit der Vorschau, blättert und lädt das Video erst auf Wunsch', async () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.fotos'))
    fireEvent.click(kachel('strand.jpg')!)

    const box = screen.getByRole('dialog', { name: 'strand.jpg' })
    expect(within(box).getByText(/1 von 3/)).toBeInTheDocument()
    await vi.waitFor(() => expect(geladen.blobs).toEqual(['1b'.padEnd(32, '0')]))
    expect(await within(box).findByRole('img', { name: 'strand.jpg' })).toBeInTheDocument()

    fireEvent.click(within(box).getByRole('button', { name: i18n.t('common.lichtbox.info') }))
    expect(within(box).getByText('Google Pixel 8')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'ArrowRight' })
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    const video = screen.getByRole('dialog', { name: 'wellen.mp4' })
    await vi.waitFor(() => expect(geladen.blobs).toContain('2b'.padEnd(32, '0')))
    expect(geladen.blobs).not.toContain('2a'.padEnd(32, '0'))
    fireEvent.click(within(video).getByRole('button', { name: i18n.t('mss.vault.fotos.abspielen') }))
    await vi.waitFor(() => expect(geladen.blobs).toContain('2a'.padEnd(32, '0')))

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('wählt aus und legt daraus ein neues Album an', async () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.fotos'))
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.fotos.auswaehlen')) }))
    fireEvent.click(kachel('strand.jpg')!)
    fireEvent.click(kachel('wellen.mp4')!)
    expect(kachel('strand.jpg')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByText(i18n.t('mss.vault.fotos.ausgewaehlt', { count: 2 }))).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.fotos.zuAlbum')) }))
    fireEvent.click(screen.getByRole('menuitem', { name: new RegExp(i18n.t('mss.vault.fotos.neuesAlbum')) }))
    await vi.waitFor(() => expect(usePromptStore.getState().pending).not.toBeNull())
    usePromptStore.getState().resolve('Meer')
    await vi.waitFor(() => expect(albumAnlegen).toHaveBeenCalledWith('Meer', ['1', '2']))
  })

  it('zeigt im Album nur, was noch da ist, und nimmt Ausgewähltes heraus', async () => {
    render(<VaultView />)
    fireEvent.click(reiter('mss.vault.ansicht.fotos'))
    fireEvent.click(reiter('mss.vault.fotos.alben'))
    fireEvent.click(screen.getByRole('button', { name: /Sommer/ }))
    expect(screen.getByRole('heading', { name: 'Sommer' })).toBeInTheDocument()
    expect(kachel('strand.jpg')).not.toBeNull()
    expect(kachel('Screenshot_20260712.png')).not.toBeNull()
    expect(kachel('geloescht.jpg')).toBeNull()
    expect(kachel('wellen.mp4')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.fotos.auswaehlen')) }))
    fireEvent.click(kachel('strand.jpg')!)
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.fotos.ausAlbum')) }))
    await vi.waitFor(() => expect(albumAendern).toHaveBeenCalledWith('al', { weg: ['1'] }))
  })
})
