/**
 * Die Galerie im Tresor nach dem UX-Durchgang (10/2026): Upload nur mit
 * Speicher, Mehrfachauswahl wie in „Dateien“, Sammelaktionen mit Fortschritt,
 * keine stillen Fehler, Rückgängig nach Papierkorb und Archiv, Ladefehler in
 * der Lichtbox mit Grund und neuem Versuch.
 */
import { act, createEvent, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { usePromptStore } from '@/stores/promptStore'
import { TresorGalerie } from './TresorGalerie'
import { useVaultStore, type VaultItem } from './vaultStore'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn(),
  biometrieEntsperren: vi.fn(),
  biometrieLoeschen: vi.fn(),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn(),
}))

const speicherAbfragen = vi.hoisted(() => vi.fn())
vi.mock('./tresorBlobApi', async (original) => ({
  ...(await original<typeof import('./tresorBlobApi')>()),
  speicherAbfragen,
}))

vi.mock('./tresorMiniaturen', () => ({
  useMiniatur: () => null,
  miniaturenVorladen: vi.fn(async () => undefined),
}))

const blobLesen = vi.hoisted(() => vi.fn())
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  blobLesen,
}))

const mehrereAufGeraetSpeichern = vi.hoisted(() => vi.fn(async () => true))
const aufGeraetSpeichern = vi.hoisted(() => vi.fn(async () => true))
vi.mock('./tresorAnzeige', async (original) => ({
  ...(await original<typeof import('./tresorAnzeige')>()),
  mehrereAufGeraetSpeichern,
  aufGeraetSpeichern,
}))

const kopf = (id: string, echt = 100) => ({ id: id.padEnd(32, '0'), groesse: 65536, echt, schluessel: 'x', loeschen: 'y' })
const medium = (id: string, service: string, typ: string, tag: number, angaben = {}): VaultItem => ({
  id,
  service,
  username: '',
  password: '',
  category: 'datei',
  createdAt: 1,
  updatedAt: 1,
  revision: 1,
  datei: { typ, original: kopf(`${id}a`, 5000), vorschau: kopf(`${id}b`), miniatur: kopf(`${id}c`), aufgenommen: Date.UTC(2026, 6, tag), ...angaben },
})

const kachel = (name: string) => screen.getByRole('button', { name })
/** Die Auswahlleiste oben; die Fußleiste für das Telefon steht im DOM dahinter. */
const kopfleiste = () => screen.getAllByRole('toolbar', { name: /ausgewählt/ })[0]
const toasts = () => useToastStore.getState().toasts

/** Langes Drücken mit dem Finger; jsdom kennt kein PointerEvent mit `pointerType`. */
async function langDruecken(el: HTMLElement) {
  const druck = createEvent.pointerDown(el, { clientX: 10, clientY: 10, button: 0 })
  Object.defineProperty(druck, 'pointerType', { value: 'touch' })
  fireEvent(el, druck)
  await new Promise((r) => setTimeout(r, 600))
  fireEvent.pointerUp(el)
}

describe('TresorGalerie', () => {
  const trashItem = vi.fn(async (_id: string) => undefined)
  const restoreItem = vi.fn(async (_id: string) => undefined)
  const setArchived = vi.fn(async (_id: string, _archiviert: boolean) => undefined)
  const albumAnlegen = vi.fn(async () => 'neu')
  const albumAendern = vi.fn(async () => undefined)
  const dateiHinzufuegen = vi.fn(async () => 'x')

  beforeEach(() => {
    for (const f of [trashItem, restoreItem, setArchived, albumAnlegen, albumAendern, dateiHinzufuegen, mehrereAufGeraetSpeichern, aufGeraetSpeichern]) f.mockClear()
    trashItem.mockImplementation(async () => undefined)
    setArchived.mockImplementation(async () => undefined)
    speicherAbfragen.mockReset()
    speicherAbfragen.mockResolvedValue({ belegt: 1024, quote: 10 * 1024 ** 3, in_loeschung: 0 })
    blobLesen.mockReset()
    blobLesen.mockResolvedValue(new Blob(['bild'], { type: 'image/webp' }))
    useToastStore.setState({ toasts: [] })
    useVaultStore.setState({
      userKey: {} as CryptoKey,
      trashItem,
      restoreItem,
      setArchived,
      albumAnlegen,
      albumAendern,
      dateiHinzufuegen,
      items: [
        medium('1', 'a.jpg', 'image/jpeg', 20),
        medium('2', 'b.jpg', 'image/jpeg', 19),
        medium('3', 'c.jpg', 'image/jpeg', 18),
        medium('4', 'clip.mp4', 'video/mp4', 17, { dauer: 75 }),
      ],
    })
  })

  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
  })

  describe('Speicher', () => {
    it('sperrt den Upload ohne Speicher, sagt warum und nimmt keine abgelegte Datei an', async () => {
      speicherAbfragen.mockResolvedValue({ belegt: 0, quote: 0, in_loeschung: 0 })
      const { container } = render(<TresorGalerie />)
      expect(await screen.findByText(i18n.t('mss.vault.dateien.keinSpeicher'))).toBeInTheDocument()
      expect(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.hochladen') })).toBeDisabled()

      const datei = new File(['x'], 'neu.jpg', { type: 'image/jpeg' })
      fireEvent.drop(container.firstElementChild!, { dataTransfer: { types: ['Files'], files: [datei] } })
      await act(async () => undefined)
      expect(dateiHinzufuegen).not.toHaveBeenCalled()
    })

    it('zeigt den belegten Speicher und meldet das Verschlüsseln als Status', async () => {
      let fertig: (id: string) => void = () => undefined
      dateiHinzufuegen.mockImplementationOnce(() => new Promise<string>((r) => (fertig = r)))
      const { container } = render(<TresorGalerie />)
      expect(await screen.findByRole('progressbar', { name: i18n.t('mss.vault.dateien.speicher') })).toBeInTheDocument()

      const feld = container.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(feld, { target: { files: [new File(['x'], 'neu.jpg', { type: 'image/jpeg' })] } })
      expect(await screen.findByRole('status')).toHaveTextContent(i18n.t('mss.vault.dateien.verschluesselt', { count: 1 }))
      await act(async () => fertig('neu'))
    })
  })

  describe('Mehrfachauswahl', () => {
    it('wählt mit Strg-Klick, Umschalt-Klick und „Alle“, ohne die Lichtbox zu öffnen', () => {
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.fotos.ausgewaehlt', { count: 1 }))

      fireEvent.click(kachel('c.jpg'), { shiftKey: true })
      expect(kachel('b.jpg')).toHaveAttribute('aria-pressed', 'true')
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.fotos.ausgewaehlt', { count: 3 }))

      fireEvent.click(within(kopfleiste()).getByRole('button', { name: new RegExp(i18n.t('mss.vault.dateien.alleAuswaehlen')) }))
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.fotos.ausgewaehlt', { count: 4 }))
    })

    it('startet die Auswahl am Finger mit langem Drücken', async () => {
      render(<TresorGalerie />)
      await langDruecken(kachel('b.jpg'))
      // Der Klick nach dem Loslassen gehört zum Druck und öffnet nichts.
      fireEvent.click(kachel('b.jpg'))
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(kachel('b.jpg')).toHaveAttribute('aria-pressed', 'true')
      // Für die Daumen gibt es die Aktionen unten noch einmal.
      expect(screen.getAllByRole('toolbar', { name: /ausgewählt/ })).toHaveLength(2)
    })

    it('speichert mehrere als ZIP', async () => {
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      fireEvent.click(kachel('clip.mp4'), { ctrlKey: true })
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.dateien.alsZip') }))
      await vi.waitFor(() => expect(mehrereAufGeraetSpeichern).toHaveBeenCalledTimes(1))
      const [dateien, , name] = mehrereAufGeraetSpeichern.mock.calls[0] as unknown as [{ pfad: string }[], CryptoKey, string]
      expect(dateien.map((d) => d.pfad)).toEqual(['a.jpg', 'clip.mp4'])
      expect(name).toBe(`${i18n.t('mss.vault.fotos.zipName')}.zip`)
      await vi.waitFor(() => expect(screen.queryByRole('toolbar')).toBeNull())
    })

    it('legt die Auswahl über die Leiste in ein neues Album', async () => {
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      fireEvent.click(kachel('c.jpg'), { ctrlKey: true })
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.fotos.zuAlbum') }))
      fireEvent.click(screen.getByRole('menuitem', { name: new RegExp(i18n.t('mss.vault.fotos.neuesAlbum')) }))
      await vi.waitFor(() => expect(usePromptStore.getState().pending).not.toBeNull())
      usePromptStore.getState().resolve('Meer')
      await vi.waitFor(() => expect(albumAnlegen).toHaveBeenCalledWith('Meer', ['1', '3']))
    })
  })

  describe('Sammelaktionen', () => {
    it('zeigt den Fortschritt und sperrt die Aktionen, solange sie laufen', async () => {
      let weiter: () => void = () => undefined
      trashItem.mockImplementationOnce(() => new Promise<undefined>((r) => (weiter = () => r(undefined))))
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      fireEvent.click(kachel('b.jpg'), { ctrlKey: true })
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.inPapierkorb') }))

      expect(await screen.findByRole('status')).toHaveTextContent(i18n.t('mss.vault.fotos.sammel.papierkorb', { count: 2 }))
      for (const name of [i18n.t('mss.vault.inPapierkorb'), i18n.t('mss.vault.archivieren'), i18n.t('mss.vault.dateien.alsZip')]) {
        expect(within(kopfleiste()).getByRole('button', { name })).toBeDisabled()
      }
      // Ein zweiter Druck startet keinen zweiten Lauf.
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.archivieren') }))
      expect(setArchived).not.toHaveBeenCalled()

      await act(async () => weiter())
      await vi.waitFor(() => expect(trashItem).toHaveBeenCalledTimes(2))
      await vi.waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    })

    it('legt in den Papierkorb und nimmt das mit Rückgängig für alle zurück', async () => {
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      fireEvent.click(kachel('b.jpg'), { ctrlKey: true })
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.inPapierkorb') }))
      await vi.waitFor(() => expect(toasts().some((t) => t.type === 'success')).toBe(true))
      const meldung = toasts().find((t) => t.type === 'success')!
      expect(meldung.message).toBe(i18n.t('mss.vault.dateien.papierkorbMehrere', { count: 2 }))
      expect(meldung.aktion?.label).toBe(i18n.t('common.undo'))
      meldung.aktion!.ausfuehren()
      await vi.waitFor(() => expect(restoreItem.mock.calls.map(([id]) => id)).toEqual(['1', '2']))
    })

    it('meldet, was sich nicht in den Papierkorb legen ließ, und macht mit dem Rest weiter', async () => {
      trashItem.mockImplementation(async (id: string) => {
        if (id === '1') throw new Error('kaputt')
      })
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      fireEvent.click(kachel('b.jpg'), { ctrlKey: true })
      fireEvent.click(within(kopfleiste()).getByRole('button', { name: i18n.t('mss.vault.inPapierkorb') }))
      await vi.waitFor(() =>
        expect(toasts().map((t) => t.message)).toContain(i18n.t('mss.vault.fotos.fehler.papierkorb', { count: 1 })),
      )
      expect(trashItem).toHaveBeenCalledWith('2')
      expect(toasts().map((t) => t.message)).toContain(i18n.t('mss.vault.inPapierkorbGelegt'))
    })
  })

  describe('Lichtbox', () => {
    const oeffnen = async () => {
      fireEvent.click(kachel('a.jpg'))
      return screen.findByRole('dialog', { name: 'a.jpg' })
    }

    it('archiviert mit „Ins Archiv gelegt“ und Rückgängig', async () => {
      render(<TresorGalerie />)
      const box = await oeffnen()
      fireEvent.click(within(box).getByRole('button', { name: i18n.t('mss.vault.archivieren') }))
      await vi.waitFor(() => expect(toasts().map((t) => t.message)).toContain(i18n.t('mss.vault.archiviert')))
      const meldung = toasts().find((t) => t.message === i18n.t('mss.vault.archiviert'))!
      meldung.aktion!.ausfuehren()
      await vi.waitFor(() => expect(setArchived).toHaveBeenLastCalledWith('1', false))
    })

    it('legt in den Papierkorb mit Rückgängig', async () => {
      render(<TresorGalerie />)
      const box = await oeffnen()
      fireEvent.click(within(box).getByRole('button', { name: i18n.t('mss.vault.inPapierkorb') }))
      await vi.waitFor(() => expect(toasts().find((t) => t.message === i18n.t('mss.vault.inPapierkorbGelegt'))?.aktion).toBeDefined())
      toasts().find((t) => t.message === i18n.t('mss.vault.inPapierkorbGelegt'))!.aktion!.ausfuehren()
      await vi.waitFor(() => expect(restoreItem).toHaveBeenCalledWith('1'))
    })

    it('meldet, wenn Archivieren scheitert', async () => {
      setArchived.mockRejectedValue(new Error('gesperrt'))
      render(<TresorGalerie />)
      const box = await oeffnen()
      fireEvent.click(within(box).getByRole('button', { name: i18n.t('mss.vault.archivieren') }))
      await vi.waitFor(() =>
        expect(toasts().find((t) => t.type === 'error')?.message).toBe(i18n.t('mss.vault.fotos.fehler.archiv', { count: 1 })),
      )
    })

    it('bietet nach einem Ladefehler „Erneut laden“ an', async () => {
      blobLesen.mockRejectedValueOnce(new Error('503'))
      render(<TresorGalerie />)
      const box = await oeffnen()
      expect(await within(box).findByText(i18n.t('mss.vault.fotos.ladeFehler'))).toBeInTheDocument()
      fireEvent.click(within(box).getByRole('button', { name: i18n.t('mss.vault.fotos.erneutLaden') }))
      expect(await within(box).findByRole('img', { name: 'a.jpg' })).toBeInTheDocument()
    })

    it('sagt ohne Netz, dass es nicht verfügbar ist, und lädt, sobald das Netz zurück ist', async () => {
      Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
      blobLesen.mockRejectedValue(new Error('offline'))
      render(<TresorGalerie />)
      const box = await oeffnen()
      expect(
        await within(box).findByText(i18n.t('mss.vault.fotos.offlineNichtDa', { aktion: i18n.t('mss.vault.dateien.offlineMachen') })),
      ).toBeInTheDocument()
      // Ohne Netz wird auch das Original versucht; das liegt vielleicht angeheftet auf dem Gerät.
      expect(blobLesen.mock.calls.map(([k]) => (k as { id: string }).id)).toEqual(['1b'.padEnd(32, '0'), '1a'.padEnd(32, '0')])
      expect(within(box).queryByRole('button', { name: i18n.t('mss.vault.fotos.erneutLaden') })).toBeNull()

      Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
      blobLesen.mockResolvedValue(new Blob(['bild'], { type: 'image/jpeg' }))
      act(() => {
        window.dispatchEvent(new Event('online'))
      })
      expect(await within(box).findByRole('img', { name: 'a.jpg' })).toBeInTheDocument()
    })
  })

  describe('Album', () => {
    it('meldet, wenn das Album nicht in den Papierkorb geht, und sagt sonst, dass die Fotos bleiben', async () => {
      useVaultStore.setState({
        items: [
          ...useVaultStore.getState().items,
          { id: 'al', service: 'Sommer', username: '', password: '', category: 'album', createdAt: 1, updatedAt: 1, revision: 1, album: { eintraege: ['1'] } },
        ],
      })
      trashItem.mockRejectedValueOnce(new Error('gesperrt'))
      render(<TresorGalerie />)
      fireEvent.click(screen.getByRole('tab', { name: new RegExp(i18n.t('mss.vault.fotos.alben')) }))
      fireEvent.click(screen.getByRole('button', { name: /Sommer/ }))

      const loeschen = () => {
        fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('mss.vault.dateien.aktionen')) }))
        fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.fotos.albumLoeschen') }))
      }
      loeschen()
      await vi.waitFor(() => expect(toasts().map((t) => t.message)).toContain(i18n.t('mss.vault.fotos.fehler.albumPapierkorb')))

      loeschen()
      await vi.waitFor(() => expect(toasts().map((t) => t.message)).toContain(i18n.t('mss.vault.fotos.albumImPapierkorb')))
      toasts().find((t) => t.message === i18n.t('mss.vault.fotos.albumImPapierkorb'))!.aktion!.ausfuehren()
      await vi.waitFor(() => expect(restoreItem).toHaveBeenCalledWith('al'))
    })
  })

  describe('Barrierefreiheit', () => {
    it('beschreibt Video-Kacheln mit Länge, der Name bleibt der Dateiname', () => {
      render(<TresorGalerie />)
      expect(kachel('clip.mp4')).toHaveAccessibleDescription(i18n.t('mss.vault.fotos.videoMitDauer', { dauer: '1:15' }))
    })

    it('zeigt die Filter als Reiter mit Auswahlzustand', () => {
      render(<TresorGalerie />)
      const filter = screen.getByRole('tablist', { name: i18n.t('mss.vault.fotos.filter') })
      expect(within(filter).getByRole('tab', { name: new RegExp(i18n.t('mss.vault.fotos.alle')) })).toHaveAttribute('aria-selected', 'true')
      fireEvent.click(within(filter).getByRole('tab', { name: new RegExp(i18n.t('mss.vault.fotos.videos')) }))
      expect(screen.queryByRole('button', { name: 'a.jpg' })).toBeNull()
      expect(kachel('clip.mp4')).toBeInTheDocument()
    })
  })
})
