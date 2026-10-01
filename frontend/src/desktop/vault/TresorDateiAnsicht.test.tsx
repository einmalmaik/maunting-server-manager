/**
 * Jede Dateiart bekommt ihre Ansicht: SVG als Bild, PDF über pdf.js, Archive
 * als Inhaltsliste, unbekannter Text im Editor, Unlesbares als Karte.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { zipSchreiben } from '@/lib/zipSchreiben'
import { TresorDateiAnsicht } from './TresorDateiAnsicht'
import { useVaultStore, type VaultItem } from './vaultStore'

const inhalt = vi.hoisted(() => ({ blob: new Blob() }))
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  blobLesen: vi.fn(async () => inhalt.blob),
  ansichtOeffnen: vi.fn(() => 'blob:ansicht'),
  ansichtSchliessen: vi.fn(),
}))

// pdf.js braucht Canvas und Worker; hier zählt nur, dass es die Bytes bekommt.
const pdfDaten = vi.hoisted(() => ({ zuletzt: null as Uint8Array | null }))
vi.mock('@/Singra/UI/PdfAnsicht', () => ({
  PdfAnsicht: ({ daten, label }: { daten: Uint8Array; label: string }) => {
    pdfDaten.zuletzt = daten
    return <div role="document" aria-label={label} />
  },
}))

vi.mock('@/components/server/FileEditorWorkspace', () => ({
  FileEditorWorkspace: ({ tabs }: { tabs: { content: string }[] }) => <textarea aria-label="inhalt" readOnly value={tabs[0].content} />,
}))

const kopf = (id: string, echt: number) => ({ id, echt, groesse: echt, loeschen: 'x' })
const datei = (service: string, typ: string, echt = 1000) =>
  ({
    id: 'd',
    service,
    category: 'datei',
    username: '',
    password: '',
    createdAt: 1,
    updatedAt: 1,
    revision: 1,
    datei: { typ, original: kopf('o', echt), vorschau: kopf('v', 0), miniatur: kopf('m', 0) },
  }) as unknown as VaultItem

function zeigen(item: VaultItem, blob: Blob) {
  inhalt.blob = blob
  useVaultStore.setState({ items: [item], userKey: {} as CryptoKey })
  return render(<TresorDateiAnsicht item={item} ort="Stammverzeichnis" folge={[item]} onWechseln={() => {}} onSchliessen={() => {}} />)
}

describe('TresorDateiAnsicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    pdfDaten.zuletzt = null
  })

  it('zeigt SVG als Bild, nicht als Dokument mit Skripten', async () => {
    const { container } = zeigen(datei('logo.svg', 'image/svg+xml'), new Blob(['<svg/>']))
    const bild = await screen.findByRole('img', { name: 'logo.svg' })
    expect(bild).toHaveAttribute('src', 'blob:ansicht')
    expect(container.ownerDocument.querySelector('iframe, object, embed')).toBeNull()
  })

  it('zeigt die Karte, wenn der Browser ein Bild nicht lesen kann', async () => {
    zeigen(datei('foto.heic', 'image/heic'), new Blob(['x']))
    fireEvent.error(await screen.findByRole('img', { name: 'foto.heic' }))
    expect(screen.getByText(i18n.t('mss.vault.dateien.formatNichtDarstellbar'))).toBeInTheDocument()
    // Einer in der Kopfleiste, einer auf der Karte.
    expect(screen.getAllByRole('button', { name: i18n.t('mss.vault.dateien.speichern') })).toHaveLength(2)
  })

  it('zeigt die Karte, wenn ein Video nicht abspielbar ist', async () => {
    const { container } = zeigen(datei('film.mkv', 'video/x-matroska'), new Blob(['x']))
    await vi.waitFor(() => expect(container.ownerDocument.querySelector('video')).not.toBeNull())
    fireEvent.error(container.ownerDocument.querySelector('video')!)
    expect(screen.getByText(i18n.t('mss.vault.dateien.formatNichtDarstellbar'))).toBeInTheDocument()
  })

  it('gibt ein PDF als Bytes an pdf.js, auch ohne MIME-Typ', async () => {
    zeigen(datei('vertrag.pdf', ''), new Blob(['%PDF-1.7']))
    expect(await screen.findByRole('document', { name: 'vertrag.pdf' })).toBeInTheDocument()
    expect(new TextDecoder().decode(pdfDaten.zuletzt!)).toBe('%PDF-1.7')
  })

  it('listet den Inhalt eines Zip-Archivs', async () => {
    const zip = zipSchreiben([
      { pfad: 'bilder/a.png', inhalt: new Uint8Array(5) },
      { pfad: 'liesmich.txt', inhalt: 'hallo' },
    ])
    zeigen(datei('paket.zip', 'application/zip'), zip)
    const liste = await screen.findByRole('list', { name: i18n.t('mss.vault.dateien.archiv.inhalt', { name: 'paket.zip' }) })
    expect(liste).toHaveTextContent('bilder/a.png')
    expect(liste).toHaveTextContent('liesmich.txt')
  })

  it('zeigt die Karte für ein kaputtes Archiv', async () => {
    zeigen(datei('kaputt.zip', 'application/zip'), new Blob(['kein zip']))
    expect(await screen.findByText(i18n.t('mss.vault.dateien.formatNichtDarstellbar'))).toBeInTheDocument()
  })

  it('öffnet eine unbekannte Datei, die Text ist, im Editor', async () => {
    zeigen(datei('Makefile.local', ''), new Blob(['all:\n\techo hallo']))
    expect(await screen.findByRole('textbox', { name: 'inhalt' })).toHaveValue('all:\n\techo hallo')
  })

  it('zeigt eine unbekannte Binärdatei als Karte', async () => {
    zeigen(datei('daten.bin', ''), new Blob([new Uint8Array([0, 1, 2, 255])]))
    expect(await screen.findByText(i18n.t('mss.vault.dateien.keineVorschau'))).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('nennt bei Office-Dateien die Art und lädt sie gar nicht erst', async () => {
    zeigen(datei('bericht.xlsx', ''), new Blob(['PK']))
    expect(await screen.findByText(new RegExp(i18n.t('mss.vault.dateien.art.tabellenblatt')))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('mss.vault.dateien.keineVorschau'))).toBeInTheDocument()
  })
})
