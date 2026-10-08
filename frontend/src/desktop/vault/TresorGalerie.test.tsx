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
import { nachbarKachel } from '@/Singra/UI/Rasterfokus'

import { TresorGalerie, anDiesemTag } from './TresorGalerie'
import { useVaultStore } from './vaultStore'
import { type VaultItem } from './vaultEintrag'

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
const offlineAnheften = vi.hoisted(() => vi.fn(async () => undefined))
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  blobLesen,
  offlineAnheften,
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

  describe('Ablegen', () => {
    // Bis 02.10.2026 zeigte die Galerie beim Ziehen nur einen Rahmen ohne Text,
    // und jedes Überfahren einer Kachel ließ ihn kurz verschwinden.
    it('sagt beim Ziehen, was mit den Dateien geschieht, auch über einer Kachel', async () => {
      const { container } = render(<TresorGalerie />)
      await screen.findByRole('progressbar', { name: i18n.t('mss.vault.dateien.speicher') })
      const flaeche = container.firstElementChild as HTMLElement
      const text = i18n.t('mss.vault.fotos.ablegenHochladen')

      fireEvent.dragOver(flaeche, { dataTransfer: { types: ['Files'], files: [] } })
      expect(screen.getByText(text)).toBeInTheDocument()

      const verlassen = (nach: Element | null) => {
        const ev = createEvent.dragLeave(flaeche)
        Object.defineProperty(ev, 'relatedTarget', { value: nach })
        fireEvent(flaeche, ev)
      }
      verlassen(within(flaeche).getAllByRole('button')[0])
      expect(screen.getByText(text)).toBeInTheDocument()

      verlassen(null)
      expect(screen.queryByText(text)).toBeNull()
    })
  })

  describe('Mehrfachauswahl', () => {
    it('wählt mit Strg-Klick, Umschalt-Klick und „Alle“, ohne die Lichtbox zu öffnen', () => {
      render(<TresorGalerie />)
      fireEvent.click(kachel('a.jpg'), { ctrlKey: true })
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 1 }))

      fireEvent.click(kachel('c.jpg'), { shiftKey: true })
      expect(kachel('b.jpg')).toHaveAttribute('aria-pressed', 'true')
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 3 }))

      fireEvent.click(within(kopfleiste()).getByRole('button', { name: new RegExp(i18n.t('mss.vault.alle')) }))
      expect(kopfleiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 4 }))
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

    it('öffnet mit Rechtsklick das Menü der Kachel und macht die Datei dort offline verfügbar', async () => {
      // Bis 03.10.2026 ging das in der Galerie nur über Auswahl oder Lichtbox.
      offlineAnheften.mockClear()
      render(<TresorGalerie />)
      fireEvent.contextMenu(kachel('b.jpg'), { clientX: 40, clientY: 40 })
      const menue = await screen.findByRole('menu', { name: i18n.t('mss.vault.dateien.aktionenFuer', { name: 'b.jpg' }) })
      expect(within(menue).getByRole('menuitem', { name: i18n.t('mss.vault.inPapierkorb') })).toBeInTheDocument()
      fireEvent.click(within(menue).getByRole('menuitem', { name: i18n.t('mss.vault.dateien.offlineMachen') }))
      await vi.waitFor(() => expect(offlineAnheften).toHaveBeenCalledWith(expect.objectContaining({ id: kopf('2a').id }), '2', expect.anything()))
      expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('öffnet am Finger kein Menü: der lange Druck bleibt bei der Auswahl', async () => {
      render(<TresorGalerie />)
      const k = kachel('b.jpg')
      const druck = createEvent.pointerDown(k, { clientX: 10, clientY: 10, button: 0 })
      Object.defineProperty(druck, 'pointerType', { value: 'touch' })
      fireEvent(k, druck)
      fireEvent.contextMenu(k)
      expect(screen.queryByRole('menu')).toBeNull()
      fireEvent.pointerUp(k)
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
      fireEvent.click(within(box).getByRole('button', { name: i18n.t('mss.vault.dateien.erneutLaden') }))
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
      expect(within(box).queryByRole('button', { name: i18n.t('mss.vault.dateien.erneutLaden') })).toBeNull()

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
      expect(within(filter).getByRole('tab', { name: new RegExp(i18n.t('mss.vault.alle')) })).toHaveAttribute('aria-selected', 'true')
      fireEvent.click(within(filter).getByRole('tab', { name: new RegExp(i18n.t('mss.vault.fotos.videos')) }))
      expect(screen.queryByRole('button', { name: 'a.jpg' })).toBeNull()
      expect(kachel('clip.mp4')).toBeInTheDocument()
    })

    it('erreicht das Raster mit einem Tab und wandert darin mit den Pfeiltasten', () => {
      // Bis 02.10.2026 führte Tab durch jedes Foto einzeln.
      render(<TresorGalerie />)
      const kacheln = ['a.jpg', 'b.jpg', 'c.jpg', 'clip.mp4'].map(kachel)
      expect(kacheln.map((k) => k.tabIndex)).toEqual([0, -1, -1, -1])

      kacheln[0].focus()
      fireEvent.keyDown(kacheln[0], { key: 'ArrowRight' })
      expect(kacheln[1]).toHaveFocus()
      expect(kacheln.map((k) => k.tabIndex)).toEqual([-1, 0, -1, -1])

      fireEvent.keyDown(kacheln[1], { key: 'End' })
      expect(kacheln[3]).toHaveFocus()
      fireEvent.keyDown(kacheln[3], { key: 'Home' })
      expect(kacheln[0]).toHaveFocus()
      // Ohne Nachbarn bleibt der Fokus stehen.
      fireEvent.keyDown(kacheln[0], { key: 'ArrowLeft' })
      expect(kacheln[0]).toHaveFocus()
    })
  })
})

describe('anDiesemTag', () => {
  const am = (id: string, ms: number, feld: 'aufgenommen' | 'geaendert' = 'aufgenommen'): VaultItem => ({
    id,
    service: `${id}.jpg`,
    username: '',
    password: '',
    category: 'datei',
    createdAt: Date.UTC(2026, 6, 20),
    updatedAt: 1,
    revision: 1,
    datei: { typ: 'image/jpeg', original: kopf(`${id}a`), vorschau: kopf(`${id}b`), miniatur: kopf(`${id}c`), [feld]: ms },
  })

  it('nimmt Fotos von diesem Kalendertag aus früheren Jahren, das jüngste Jahr zuerst', () => {
    const heute = new Date(2027, 6, 20, 9, 0)
    const gruppen = anDiesemTag(
      [
        am('vor1', Date.UTC(2026, 6, 20, 23, 30)),
        am('vor3', Date.UTC(2024, 6, 20, 8)),
        am('auchVor1', new Date(2026, 6, 20, 12).getTime(), 'geaendert'),
        am('gestern', Date.UTC(2026, 6, 19, 12)),
        am('heute', Date.UTC(2027, 6, 20, 7)),
      ],
      heute,
    )
    expect(gruppen.map((g) => [g.jahre, g.items.map((i) => i.id)])).toEqual([
      [1, ['vor1', 'auchVor1']],
      [3, ['vor3']],
    ])
  })

  it('zählt das Hochladen nicht als Erinnerung', () => {
    const ohneDatum = { ...am('x', 0), datei: { typ: 'image/jpeg', original: kopf('xa'), vorschau: kopf('xb'), miniatur: kopf('xc') } }
    expect(anDiesemTag([ohneDatum], new Date(2027, 6, 20))).toEqual([])
  })

  it('zeigt die Karte in „Alle“ und öffnet das Foto', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2027, 6, 20, 9, 0))
    try {
      useVaultStore.setState({
        userKey: {} as CryptoKey,
        items: [am('vor1', Date.UTC(2026, 6, 20, 10)), am('anders', Date.UTC(2026, 6, 3, 10))],
      })
      render(<TresorGalerie />)
      const tag = screen.getByRole('region', { name: 'An diesem Tag' })
      fireEvent.click(within(tag).getByRole('button', { name: 'Vor einem Jahr, 1 Element' }))
      expect(await screen.findByRole('dialog')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('nachbarKachel', () => {
  /** Ein Raster aus festen Kästen; jsdom misst sonst alles mit 0. */
  function raster(kaesten: Array<[number, number]>) {
    const wurzel = document.createElement('div')
    const kacheln = kaesten.map(([links, oben], i) => {
      const k = document.createElement('button')
      k.dataset.kachel = String(i)
      k.getBoundingClientRect = () => ({ left: links, top: oben, width: 100, height: 100, right: links + 100, bottom: oben + 100, x: links, y: oben, toJSON: () => ({}) })
      wurzel.append(k)
      return k
    })
    return { wurzel, kacheln }
  }

  it('geht hoch und runter zur Kachel unter der Mitte, auch in eine kürzere Zeile', () => {
    // Drei Spalten; die letzte Zeile hat nur zwei Kacheln, danach ein neuer Monat.
    const { wurzel, kacheln } = raster([
      [0, 0], [104, 0], [208, 0],
      [0, 104], [104, 104],
      [0, 240], [104, 240], [208, 240],
    ])
    expect(nachbarKachel(wurzel, kacheln[1], 'ArrowDown')).toBe(kacheln[4])
    expect(nachbarKachel(wurzel, kacheln[2], 'ArrowDown')).toBe(kacheln[4])
    expect(nachbarKachel(wurzel, kacheln[4], 'ArrowDown')).toBe(kacheln[6])
    expect(nachbarKachel(wurzel, kacheln[7], 'ArrowUp')).toBe(kacheln[4])
    expect(nachbarKachel(wurzel, kacheln[0], 'ArrowUp')).toBeNull()
    expect(nachbarKachel(wurzel, kacheln[7], 'ArrowDown')).toBeNull()
  })
})
