/**
 * Der Dateibereich wie im Explorer: Ziehen auf Ordner, Pfadleiste und Baum,
 * Dateien vom Rechner in einen Ordner, Rechtsklick, Ansicht und Editor.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { TresorDateiBereich, ZIEH_TYP } from './TresorDateiBereich'
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

vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  angeheftet: vi.fn().mockResolvedValue(new Set()),
  blobLesen: vi.fn(async () => new Blob(['Zeile eins'], { type: 'text/plain' })),
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

describe('TresorDateiBereich', () => {
  const saveItem = vi.fn(async (..._args: unknown[]) => undefined)
  const dateiHinzufuegen = vi.fn(async (..._args: unknown[]) => 'neu')

  beforeEach(async () => {
    await i18n.changeLanguage('de')
    saveItem.mockClear()
    dateiHinzufuegen.mockClear()
    useVaultStore.setState({
      items: [vertraege, fotos, miete, pdf, notiz, beleg],
      userKey: {} as CryptoKey,
      saveItem: saveItem as never,
      dateiHinzufuegen: dateiHinzufuegen as never,
    })
  })

  it('verschiebt eine Datei, die auf einen Ordner gezogen wird', async () => {
    render(<TresorDateiBereich />)
    const dt = transfer()
    fireEvent.dragStart(zeile('vertrag.pdf'), { dataTransfer: dt })
    expect(dt.getData(ZIEH_TYP)).toBe('p')
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

  it('zeigt eine Datei ohne Vorschau mit Speichern in der Kopfleiste', () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('vertrag.pdf'))
    const ansicht = screen.getByRole('dialog', { name: 'vertrag.pdf' })
    const kopfleiste = ansicht.querySelector('header')!
    expect(within(kopfleiste).getByRole('button', { name: i18n.t('mss.vault.dateien.speichern') })).toBeInTheDocument()
    expect(within(ansicht).getByText(i18n.t('mss.vault.dateien.keineVorschau'))).toBeInTheDocument()
  })

  it('öffnet eine Textdatei gleich im Editor, mit ihrem Ort', async () => {
    render(<TresorDateiBereich />)
    fireEvent.click(eintrag('notiz.txt'))
    expect(await screen.findByTestId('tresor-texteditor')).toBeInTheDocument()
    expect(await screen.findByLabelText('inhalt')).toHaveValue('Zeile eins')
    expect(screen.getByText(`${i18n.t('mss.vault.dateien.stamm')} / notiz.txt`)).toBeInTheDocument()
  })
})
