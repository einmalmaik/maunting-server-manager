/**
 * Der Dateibereich wie im Explorer: Ziehen auf Ordner, Pfadleiste und Baum,
 * Dateien vom Rechner in einen Ordner, Rechtsklick, Ansicht und Editor,
 * Mehrfachauswahl mit Sammelaktionen.
 */
import { act, createEvent, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { usePromptStore } from '@/stores/promptStore'
import { TresorDateiBereich, ZIEH_TYP } from './TresorDateiBereich'
import { useTresorUploads } from './tresorDateien'
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

vi.mock('./tresorBlobApi', async (original) => ({
  ...(await original<typeof import('./tresorBlobApi')>()),
  speicherAbfragen: vi.fn().mockResolvedValue({ belegt: 0, quote: 10 * 1024 ** 3 }),
}))

const offlineAnheften = vi.hoisted(() => vi.fn(async () => undefined))
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  angeheftet: vi.fn().mockResolvedValue(new Set()),
  offlineAnheften,
  blobLesen: vi.fn(async () => new Blob(['Zeile eins'], { type: 'text/plain' })),
}))

const mehrereAufGeraetSpeichern = vi.hoisted(() => vi.fn(async () => true))
vi.mock('./tresorAnzeige', async (original) => ({
  ...(await original<typeof import('./tresorAnzeige')>()),
  mehrereAufGeraetSpeichern,
}))

// CodeMirror braucht ein Layout, das jsdom nicht hat.
vi.mock('@/components/server/FileEditorWorkspace', () => ({
  FileEditorWorkspace: ({ tabs, ortLabel }: { tabs: { content: string }[]; ortLabel?: string }) => (
    <div>
      <p>{ortLabel}</p>
      <textarea aria-label="inhalt" readOnly value={tabs[0].content} />
    </div>
  ),
}))

const kopf = (id: string, echt: number) => ({ id, echt, groesse: echt, loeschen: 'x' })
const basis = { username: '', password: '', createdAt: 1, updatedAt: 1, revision: 1 }
const ordner = (id: string, service: string, eltern?: string) => ({ ...basis, id, service, category: 'ordner', ordner: eltern }) as VaultItem
const datei = (id: string, service: string, typ: string, eltern?: string) =>
  ({ ...basis, id, service, category: 'datei', ordner: eltern, datei: { typ, original: kopf(`o-${id}`, 1000), vorschau: kopf(`v-${id}`, 0), miniatur: kopf(`m-${id}`, 0) } }) as unknown as VaultItem

const vertraege = ordner('v', 'Verträge')
const fotos = ordner('f', 'Fotos')
const miete = ordner('m', 'Miete', 'v')
const pdf = datei('p', 'vertrag.pdf', 'application/pdf')
const notiz = datei('n', 'notiz.txt', 'text/plain')
const beleg = datei('b', 'beleg.pdf', 'application/pdf', 'v')
const brief = datei('w', 'brief.docx', '')

/** Ein DataTransfer, der zwischen dragStart und drop dasselbe behält. */
function transfer(dateien: File[] = []) {
  const daten = new Map<string, string>()
  return {
    get types() {
      return dateien.length ? ['Files'] : [...daten.keys()]
    },
    setData: (typ: string, wert: string) => daten.set(typ, wert),
    getData: (typ: string) => daten.get(typ) ?? '',
    files: dateien,
    effectAllowed: 'all',
  }
}

/** Der Öffnen-Knopf einer Zeile in der Liste (nicht im Baum, nicht das Menü der Zeile). */
const eintrag = (name: string) =>
  within(screen.getByRole('list', { name: i18n.t('mss.vault.dateien.inhalt') })).getByRole('button', { name: new RegExp(`^${name}`) })
const zeile = (name: string) => eintrag(name).closest('li')!
const auswahlLeiste = () => screen.getAllByRole('toolbar', { name: /ausgewählt/ })[0]

/** Langes Drücken mit dem Finger; jsdom kennt kein PointerEvent mit `pointerType`. */
async function langDruecken(el: HTMLElement) {
  const druck = createEvent.pointerDown(el, { clientX: 10, clientY: 10, button: 0 })
  Object.defineProperty(druck, 'pointerType', { value: 'touch' })
  fireEvent(el, druck)
  await new Promise((r) => setTimeout(r, 600))
  fireEvent.pointerUp(el)
}

describe('TresorDateiBereich', () => {
  const saveItem = vi.fn(async (..._args: unknown[]) => undefined)
  const dateiHinzufuegen = vi.fn(async (..._args: unknown[]) => 'neu')
  const trashItem = vi.fn(async (..._args: unknown[]) => undefined)
  const restoreItem = vi.fn(async (..._args: unknown[]) => undefined)
  const setArchived = vi.fn(async (..._args: unknown[]) => undefined)
  const ordnerAnlegen = vi.fn(async (..._args: unknown[]) => 'neu')

  beforeEach(async () => {
    await i18n.changeLanguage('de')
    saveItem.mockReset().mockResolvedValue(undefined)
    dateiHinzufuegen.mockClear()
    trashItem.mockReset().mockResolvedValue(undefined)
    restoreItem.mockReset().mockResolvedValue(undefined)
    setArchived.mockReset().mockResolvedValue(undefined)
    ordnerAnlegen.mockReset().mockResolvedValue('neu')
    useToastStore.setState({ toasts: [] })
    usePromptStore.setState({ pending: null })
    useTresorUploads.setState({ je: {} })
    offlineAnheften.mockClear()
    mehrereAufGeraetSpeichern.mockClear()
    useVaultStore.setState({
      items: [vertraege, fotos, miete, pdf, notiz, beleg, brief],
      userKey: {} as CryptoKey,
      saveItem: saveItem as never,
      trashItem: trashItem as never,
      restoreItem: restoreItem as never,
      setArchived: setArchived as never,
      ordnerAnlegen: ordnerAnlegen as never,
      dateiHinzufuegen: dateiHinzufuegen as never,
    })
  })

  it('verschiebt eine Datei, die auf einen Ordner gezogen wird', async () => {
    render(<TresorDateiBereich />)
    const dt = transfer()
    fireEvent.dragStart(zeile('vertrag.pdf'), { dataTransfer: dt })
    expect(JSON.parse(dt.getData(ZIEH_TYP))).toEqual(['p'])
    const ziel = zeile('Verträge')
    expect(fireEvent.dragOver(ziel, { dataTransfer: dt })).toBe(false)
    fireEvent.drop(ziel, { dataTransfer: dt })
    await vi.waitFor(() => expect(saveItem).toHaveBeenCalledWith(expect.objectContaining({ id: 'p', ordner: 'v' })))
  })

  it('lässt einen Ordner nicht auf sich selbst fallen', () => {
    render(<TresorDateiBereich />)
    const dt = transfer()
    fireEvent.dragStart(zeile('Verträge'), { dataTransfer: dt })
    expect(fireEvent.dragOver(zeile('Verträge'), { dataTransfer: dt })).toBe(true)
    fireEvent.drop(zeile('Verträge'), { dataTransfer: dt })
    expect(saveItem).not.toHaveBeenCalled()
  })

  it('verschiebt über die Pfadleiste nach oben', async () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('Verträge'))
    const dt = transfer()
    fireEvent.dragStart(await vi.waitFor(() => zeile('beleg.pdf')), { dataTransfer: dt })
    const pfad = screen.getByRole('navigation', { name: i18n.t('mss.vault.dateien.pfad') })
    const stamm = within(pfad).getByRole('button', { name: i18n.t('mss.vault.dateien.stamm') })
    fireEvent.dragOver(stamm, { dataTransfer: dt })
    fireEvent.drop(stamm, { dataTransfer: dt })
    await vi.waitFor(() => expect(saveItem).toHaveBeenCalledWith(expect.objectContaining({ id: 'b', ordner: undefined })))
  })

  it('lädt Dateien vom Rechner in den Ordner, auf dem sie landen', async () => {
    render(<TresorDateiBereich />)
    const neu = new File(['x'], 'scan.png', { type: 'image/png' })
    const dt = transfer([neu])
    await vi.waitFor(() => expect(fireEvent.dragOver(zeile('Fotos'), { dataTransfer: dt })).toBe(false))
    fireEvent.drop(zeile('Fotos'), { dataTransfer: dt })
    await vi.waitFor(() => expect(dateiHinzufuegen).toHaveBeenCalledWith(neu, 'f'))
  })

  it('öffnet per Rechtsklick dasselbe Menü wie der Knopf der Zeile', () => {
    render(<TresorDateiBereich />)
    fireEvent.contextMenu(zeile('vertrag.pdf'))
    const menue = screen.getByRole('menu', { name: i18n.t('mss.vault.dateien.aktionen') })
    expect(within(menue).getByRole('menuitem', { name: i18n.t('mss.vault.dateien.umbenennen') })).toBeInTheDocument()
    expect(within(menue).getByRole('menuitem', { name: i18n.t('mss.vault.dateien.speichern') })).toBeInTheDocument()
  })

  it('zeigt eine Datei ohne Vorschau mit Art und Speichern in der Kopfleiste', () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('brief.docx'))
    const ansicht = screen.getByRole('dialog', { name: 'brief.docx' })
    const kopfleiste = ansicht.querySelector('header')!
    expect(within(kopfleiste).getByRole('button', { name: i18n.t('mss.vault.dateien.speichern') })).toBeInTheDocument()
    expect(within(ansicht).getByText(i18n.t('mss.vault.dateien.keineVorschau'))).toBeInTheDocument()
    expect(within(ansicht).getByText(new RegExp(i18n.t('mss.vault.dateien.art.dokument')))).toBeInTheDocument()
  })

  describe('Mehrfachauswahl', () => {
    it('wählt mit Strg-Klick, ohne zu öffnen, und ein Klick schaltet danach nur um', () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 1 }))
      fireEvent.click(eintrag('notiz.txt'))
      expect(screen.queryByTestId('tresor-texteditor')).not.toBeInTheDocument()
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 2 }))
      fireEvent.click(eintrag('notiz.txt'))
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 1 }))
    })

    it('wählt mit Umschalt einen Bereich und mit Strg+A alles; Escape hebt auf', () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('Fotos'), { ctrlKey: true })
      fireEvent.click(eintrag('notiz.txt'), { shiftKey: true })
      // Reihenfolge: Ordner (Fotos, Verträge), dann Dateien (brief, notiz, vertrag).
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 4 }))
      fireEvent.keyDown(eintrag('notiz.txt'), { key: 'a', ctrlKey: true })
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 5 }))
      fireEvent.keyDown(eintrag('notiz.txt'), { key: 'Escape' })
      expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
    })

    it('legt mit Entf alle gewählten in den Papierkorb', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      fireEvent.click(eintrag('Fotos'), { ctrlKey: true })
      fireEvent.keyDown(eintrag('Fotos'), { key: 'Delete' })
      await vi.waitFor(() => expect(trashItem.mock.calls.map((c) => c[0]).sort()).toEqual(['f', 'p']))
    })

    it('zieht alle gewählten zusammen auf einen Ordner', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      fireEvent.click(eintrag('notiz.txt'), { ctrlKey: true })
      const dt = transfer()
      fireEvent.dragStart(zeile('notiz.txt'), { dataTransfer: dt })
      expect(JSON.parse(dt.getData(ZIEH_TYP)).sort()).toEqual(['n', 'p'])
      fireEvent.dragOver(zeile('Fotos'), { dataTransfer: dt })
      fireEvent.drop(zeile('Fotos'), { dataTransfer: dt })
      await vi.waitFor(() => expect(saveItem).toHaveBeenCalledTimes(2))
      expect(saveItem.mock.calls.map((c) => [(c[0] as VaultItem).id, (c[0] as VaultItem).ordner]).sort()).toEqual([
        ['n', 'f'],
        ['p', 'f'],
      ])
    })

    it('lässt einen gewählten Ordner nicht in einen anderen gewählten fallen', () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('Fotos'), { ctrlKey: true })
      fireEvent.click(eintrag('Verträge'), { ctrlKey: true })
      const dt = transfer()
      fireEvent.dragStart(zeile('Fotos'), { dataTransfer: dt })
      expect(fireEvent.dragOver(zeile('Verträge'), { dataTransfer: dt })).toBe(true)
    })

    it('startet die Auswahl am Finger mit langem Drücken und öffnet dabei nichts', async () => {
      render(<TresorDateiBereich />)
      await langDruecken(zeile('notiz.txt'))
      fireEvent.click(eintrag('notiz.txt'))
      expect(screen.queryByTestId('tresor-texteditor')).not.toBeInTheDocument()
      expect(auswahlLeiste()).toHaveAccessibleName(i18n.t('mss.vault.dateien.ausgewaehlt', { count: 1 }))
    })

    it('startet am Finger kein Ziehen, das hängen bliebe (Android beim langen Drücken)', () => {
      render(<TresorDateiBereich />)
      const li = zeile('notiz.txt')
      const druck = createEvent.pointerDown(li, { clientX: 10, clientY: 10, button: 0 })
      Object.defineProperty(druck, 'pointerType', { value: 'touch' })
      fireEvent(li, druck)
      const dt = transfer()
      expect(fireEvent.dragStart(li, { dataTransfer: dt })).toBe(false)
      expect(dt.types).toEqual([])
      expect(li).not.toHaveClass('opacity-50')
      // Mit der Maus bleibt Ziehen wie gewohnt.
      const maus = createEvent.pointerDown(li, { clientX: 10, clientY: 10, button: 0 })
      Object.defineProperty(maus, 'pointerType', { value: 'mouse' })
      fireEvent(li, maus)
      expect(fireEvent.dragStart(li, { dataTransfer: transfer() })).toBe(true)
    })

    it('beendet die Auswahl mit der Zurück-Taste', async () => {
      await vi.waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      await vi.waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
      act(() => window.history.back())
      await vi.waitFor(() => expect(screen.queryAllByRole('toolbar')).toHaveLength(0))
    })

    it('hebt die Auswahl beim Ordnerwechsel auf', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      const pfad = screen.getByRole('navigation', { name: i18n.t('mss.vault.dateien.pfad') })
      fireEvent.contextMenu(zeile('Verträge'))
      fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.dateien.oeffnen') }))
      await vi.waitFor(() => expect(within(pfad).getByRole('button', { name: 'Verträge' })).toBeInTheDocument())
      expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
    })

    it('speichert Ordner und Dateien als ein Zip mit Pfaden', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('Verträge'), { ctrlKey: true })
      fireEvent.click(eintrag('notiz.txt'), { ctrlKey: true })
      fireEvent.click(within(auswahlLeiste()).getByRole('button', { name: i18n.t('mss.vault.dateien.speichern') }))
      await vi.waitFor(() => expect(mehrereAufGeraetSpeichern).toHaveBeenCalledTimes(1))
      const [dateien, , name] = mehrereAufGeraetSpeichern.mock.calls[0] as unknown as [{ pfad: string }[], unknown, string]
      expect(dateien.map((d) => d.pfad).sort()).toEqual(['Verträge/beleg.pdf', 'notiz.txt'])
      expect(name).toBe(`${i18n.t('mss.vault.dateien.zipStamm')}.zip`)
    })

    it('macht einen gewählten Ordner samt Inhalt offline verfügbar', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('Verträge'), { ctrlKey: true })
      fireEvent.click(within(auswahlLeiste()).getByRole('button', { name: i18n.t('mss.vault.dateien.offlineMachen') }))
      await vi.waitFor(() => expect(offlineAnheften).toHaveBeenCalledTimes(1))
      expect((offlineAnheften.mock.calls[0] as unknown as [{ id: string }])[0].id).toBe('o-b')
    })
  })

  describe('Suche', () => {
    const treffer = () => screen.getByRole('list', { name: i18n.t('mss.vault.dateien.treffer') })

    it('findet Dateien in allen Ordnern, ohne Papierkorb und Archiv, und nennt ihren Ort', () => {
      const geloescht = { ...datei('x', 'beleg-alt.pdf', 'application/pdf'), trashedAt: 5 } as VaultItem
      const archiv = { ...ordner('a', 'Altes'), archivedAt: 5 } as VaultItem
      const imArchiv = datei('y', 'beleg-2019.pdf', 'application/pdf', 'a')
      useVaultStore.setState({ items: [vertraege, fotos, miete, pdf, notiz, beleg, brief, geloescht, archiv, imArchiv] })
      render(<TresorDateiBereich suche="  BELEG " />)
      const liste = treffer()
      expect(within(liste).getAllByRole('listitem')).toHaveLength(1)
      expect(within(liste).getByRole('button', { name: /^beleg\.pdf/ })).toHaveTextContent(`${i18n.t('mss.vault.dateien.stamm')} / Verträge`)
      expect(screen.getByText(i18n.t('mss.vault.dateien.trefferAnzahl', { count: 1 }))).toBeInTheDocument()
    })

    it('findet auch Unterordner und zeigt nach dem Öffnen ihren Inhalt', async () => {
      useVaultStore.setState({ items: [vertraege, fotos, miete, pdf, notiz, beleg, brief, datei('k', 'kaution.pdf', 'application/pdf', 'm')] })
      render(<TresorDateiBereich suche="miete" />)
      const ordnerZeile = within(treffer()).getByRole('button', { name: /^Miete/ })
      expect(ordnerZeile).toHaveTextContent(`${i18n.t('mss.vault.dateien.stamm')} / Verträge`)
      fireEvent.click(ordnerZeile)
      await vi.waitFor(() => expect(eintrag('kaution.pdf')).toBeInTheDocument())
    })

    it('hat einen eigenen Leerzustand, wenn nichts passt', () => {
      render(<TresorDateiBereich suche="gibt-es-nicht" />)
      expect(screen.getByText(i18n.t('mss.vault.dateien.sucheLeerTitel'))).toBeInTheDocument()
      expect(screen.queryByText(i18n.t('mss.vault.dateien.leerTitel'))).not.toBeInTheDocument()
    })
  })

  describe('Fehler und Rückgängig', () => {
    const letzterToast = () => useToastStore.getState().toasts.at(-1)

    it('meldet, wenn der Papierkorb scheitert, statt still zu bleiben', async () => {
      trashItem.mockRejectedValueOnce('kaputt')
      render(<TresorDateiBereich />)
      fireEvent.contextMenu(zeile('vertrag.pdf'))
      fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.inPapierkorb') }))
      await vi.waitFor(() => expect(letzterToast()).toMatchObject({ type: 'error', message: i18n.t('mss.vault.dateien.papierkorbFehler') }))
    })

    it('meldet, wenn Archivieren, Umbenennen oder ein neuer Ordner scheitert', async () => {
      setArchived.mockRejectedValueOnce(new Error('Tresor gesperrt'))
      render(<TresorDateiBereich />)
      fireEvent.contextMenu(zeile('vertrag.pdf'))
      fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.archivieren') }))
      await vi.waitFor(() => expect(letzterToast()).toMatchObject({ type: 'error', message: 'Tresor gesperrt' }))

      saveItem.mockRejectedValueOnce('kaputt')
      fireEvent.contextMenu(zeile('vertrag.pdf'))
      fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.dateien.umbenennen') }))
      await vi.waitFor(() => expect(usePromptStore.getState().pending).not.toBeNull())
      act(() => usePromptStore.getState().resolve('neu.pdf'))
      await vi.waitFor(() => expect(letzterToast()).toMatchObject({ type: 'error', message: i18n.t('mss.vault.dateien.umbenennenFehler') }))

      ordnerAnlegen.mockRejectedValueOnce('kaputt')
      fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.ordnerAnlegen') }))
      await vi.waitFor(() => expect(usePromptStore.getState().pending).not.toBeNull())
      act(() => usePromptStore.getState().resolve('Neu'))
      await vi.waitFor(() => expect(letzterToast()).toMatchObject({ type: 'error', message: i18n.t('mss.vault.dateien.ordnerFehler') }))
    })

    it('holt nach Entf alle gewählten mit „Rückgängig“ zurück', async () => {
      render(<TresorDateiBereich />)
      fireEvent.click(eintrag('vertrag.pdf'), { ctrlKey: true })
      fireEvent.click(eintrag('Fotos'), { ctrlKey: true })
      fireEvent.keyDown(eintrag('Fotos'), { key: 'Delete' })
      await vi.waitFor(() => expect(letzterToast()?.aktion?.label).toBe(i18n.t('common.undo')))
      expect(letzterToast()?.message).toBe(i18n.t('mss.vault.dateien.papierkorbMehrere', { count: 2 }))
      act(() => letzterToast()!.aktion!.ausfuehren())
      await vi.waitFor(() => expect(restoreItem.mock.calls.map((c) => c[0]).sort()).toEqual(['f', 'p']))
    })

    it('macht Archivieren rückgängig, auch für einen einzelnen Eintrag', async () => {
      render(<TresorDateiBereich />)
      fireEvent.contextMenu(zeile('notiz.txt'))
      fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('mss.vault.archivieren') }))
      await vi.waitFor(() => expect(letzterToast()).toMatchObject({ type: 'success', message: i18n.t('mss.vault.archiviert') }))
      act(() => letzterToast()!.aktion!.ausfuehren())
      await vi.waitFor(() => expect(setArchived).toHaveBeenCalledWith('n', false))
    })
  })

  describe('Aktionsmenü am Telefon', () => {
    const telefon = (ja: boolean) =>
      vi.stubGlobal('matchMedia', (frage: string) => ({
        matches: ja && frage.includes('max-width'),
        media: frage,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))

    afterEach(() => vi.unstubAllGlobals())

    it('öffnet über „…“ ein Blatt von unten statt eines Flyouts', async () => {
      telefon(true)
      render(<TresorDateiBereich />)
      const name = i18n.t('mss.vault.dateien.aktionenFuer', { name: 'vertrag.pdf' })
      fireEvent.click(screen.getByRole('button', { name }))
      const blatt = screen.getByRole('dialog', { name })
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      fireEvent.click(within(blatt).getByRole('button', { name: i18n.t('mss.vault.inPapierkorb') }))
      await vi.waitFor(() => expect(trashItem).toHaveBeenCalledWith('p'))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('bleibt am Rechner beim Flyout am Knopf', () => {
      telefon(false)
      render(<TresorDateiBereich />)
      fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.aktionenFuer', { name: 'vertrag.pdf' }) }))
      expect(screen.getByRole('menu', { name: i18n.t('mss.vault.dateien.aktionen') })).toBeInTheDocument()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('verspricht im leeren Ordner das Hineinziehen nur am Rechner', async () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('Fotos'))
    expect(await screen.findByText(i18n.t('mss.vault.dateien.leerTitel'))).toBeInTheDocument()
    // Der immer sichtbare Teil gilt auch am Telefon, wo es kein Ziehen gibt.
    expect(i18n.t('mss.vault.dateien.leer')).not.toMatch(/zieh/i)
    expect(screen.getByText(i18n.t('mss.vault.dateien.leerZiehen'))).toHaveClass('hidden', 'md:inline')
  })

  it('nennt die Speichern-Aktion der Auswahl anders als das Speichern im Editor', () => {
    expect(i18n.t('mss.vault.dateien.kurz.speichern')).not.toBe(i18n.t('common.save'))
  })

  it('zeigt laufende Uploads oben im Bereich', () => {
    useTresorUploads.setState({ je: { n: { gesendet: 1, gesamt: 4 } } })
    render(<TresorDateiBereich />)
    expect(screen.getByText(i18n.t('mss.vault.dateien.uploadStand', { count: 1 }))).toBeInTheDocument()
  })

  it('öffnet eine Textdatei gleich im Editor, mit ihrem Ort', async () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('notiz.txt'))
    expect(await screen.findByTestId('tresor-texteditor')).toBeInTheDocument()
    expect(await screen.findByLabelText('inhalt')).toHaveValue('Zeile eins')
    expect(screen.getByText(`${i18n.t('mss.vault.dateien.stamm')} / notiz.txt`)).toBeInTheDocument()
  })
})
