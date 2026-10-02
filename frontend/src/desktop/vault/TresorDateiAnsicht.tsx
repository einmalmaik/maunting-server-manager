/**
 * Eine Tresor-Datei ansehen: in der Lichtbox der Design-DNA, wie Fotos in der
 * Galerie. Die Aktionen stehen oben in der Kopfleiste, Details und frühere
 * Fassungen in der Infoleiste. Textdateien öffnen gleich im Editor.
 *
 * Was gezeigt wird, sagt `vorschauArt`: Bilder (auch SVG, als <img>, ohne
 * Skripte), Video, Audio, PDF, Archive (Inhaltsliste), Schriften (Probetext).
 * Eine unbekannte Datei bis 1 MB, die sich als Text lesen lässt, geht im
 * Editor auf. Der Rest bekommt eine Karte zum Speichern.
 *
 * Entschlüsselt wird auf dem Gerät; die Objekt-URL verfällt beim Wechsel, beim
 * Schließen und beim Sperren.
 */

import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Crop,
  Download,
  File as DateiIcon,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileType,
  FileVideo,
  Presentation,
  RotateCw,
  Trash2,
  WifiOff,
} from 'lucide-react'
import { Button, Lichtbox, ProgressBar, Versionsliste } from '@/Singra/UI'
import { archivInhalt, type ArchivEintrag } from '@/lib/zipLesen'
import { toast } from '@/stores/toastStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useVaultStore, type VaultItem } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, blobLesen } from './tresorDateien'
import { artBezeichnung, aufGeraetSpeichern, istText, vorschauArt, type VorschauArt } from './tresorAnzeige'
import { fassungenVon } from './tresorOrdner'
import { BEARBEITBAR, TresorBildeditor } from './TresorBildeditor'
import { TresorTexteditor } from './TresorTexteditor'
import { Archivliste } from './vorschau/Archivliste'
import { Schriftprobe } from './vorschau/Schriftprobe'

const PdfAnsicht = lazy(() => import('@/Singra/UI/PdfAnsicht').then((m) => ({ default: m.PdfAnsicht })))

/** Bis zu dieser Größe öffnet eine Textdatei im Editor, größere werden gespeichert. */
export const TEXT_HOECHSTENS = 1024 * 1024
/** PDFs, Archive und Schriften liegen zum Zeigen ganz im Speicher; darüber nur die Karte. */
export const GANZ_HOECHSTENS = 256 * 1024 * 1024

const TEXTARTIG: (VorschauArt | null)[] = ['text', 'markdown', 'tabelle']

export function dateiIcon(typ: string, name = '') {
  switch (vorschauArt(typ, name)) {
    case 'bild':
      return FileImage
    case 'video':
      return FileVideo
    case 'audio':
      return FileAudio
    case 'text':
      return FileCode
    case 'markdown':
    case 'pdf':
      return FileText
    case 'tabelle':
      return FileSpreadsheet
    case 'archiv':
      return FileArchive
    case 'schrift':
      return FileType
    case 'office': {
      const art = artBezeichnung(typ, name)
      return art === 'tabellenblatt' ? FileSpreadsheet : art === 'praesentation' ? Presentation : FileText
    }
    default:
      return DateiIcon
  }
}

/** Ob eine Datei direkt im Editor statt in der Lichtbox aufgeht. */
export function oeffnetImEditor(item: VaultItem): boolean {
  return !!item.datei && TEXTARTIG.includes(vorschauArt(item.datei.typ, item.service)) && item.datei.original.echt <= TEXT_HOECHSTENS
}

interface Anzeige {
  id: string
  url: string | null
  text: string | null
  /** Bytes für PDF und Schriften. */
  bytes: Uint8Array | null
  archiv: ArchivEintrag[] | null
  anteil: number | null
  fehler: boolean
  /** Der Fehler kam ohne Netz: die Datei liegt nicht auf diesem Gerät. */
  offline: boolean
  /** Geladen, aber hier nicht darstellbar (Bildformat, Codec, kaputtes Archiv). */
  nichtDarstellbar: boolean
}

const LEER = { url: null, text: null, bytes: null, archiv: null, anteil: 0, fehler: false, offline: false, nichtDarstellbar: false }

interface Props {
  item: VaultItem
  /** Ort der Datei, etwa „Stammverzeichnis / Verträge“. */
  ort: string
  /** Dateien, durch die man blättern kann (ohne Textdateien, die im Editor aufgehen). */
  folge: VaultItem[]
  onWechseln: (item: VaultItem) => void
  onSchliessen: () => void
}

// Am Telefon 44 px Tippfläche; `min-*` schlägt die Höhe aus `size`.
const KNOPF = 'min-h-11 min-w-11 sm:min-h-8 sm:min-w-8 text-white/85 hover:bg-white/10 hover:text-white'

export function TresorDateiAnsicht({ item: anfang, ort, folge, onWechseln, onSchliessen }: Props) {
  const { t, i18n } = useTranslation()
  const userKey = useVaultStore((s) => s.userKey)
  const trashItem = useVaultStore((s) => s.trashItem)
  const restoreItem = useVaultStore((s) => s.restoreItem)
  const fassungZurueckholen = useVaultStore((s) => s.fassungZurueckholen)
  // Live aus dem Store: nach Bearbeiten oder Zurückholen hat die Datei ein neues Original.
  const item = useVaultStore((s) => s.items.find((i) => i.id === anfang.id)) ?? anfang
  const datei = item.datei
  const art = datei ? vorschauArt(datei.typ, item.service) : null
  const groesse = datei?.original.echt ?? 0
  // Unbekannt und klein: vielleicht Text. Das zeigt erst der Inhalt.
  const textartig = TEXTARTIG.includes(art) || (art === null && groesse <= TEXT_HOECHSTENS)
  const [anzeige, setAnzeige] = useState<Anzeige>({ id: item.id, ...LEER })
  const [bildBearbeiten, setBildBearbeiten] = useState(false)
  const [holt, setHolt] = useState<string | null>(null)
  /** Zählt „Erneut laden“ hoch und lädt damit neu. */
  const [versuch, setVersuch] = useState(0)

  // Ein neues Original (nach Bearbeiten oder Zurückholen) lädt neu. Im
  // Texteditor nicht: er hält den Text selbst, ein Neuladen würfe ihn samt
  // Rückgängig-Verlauf weg.
  const ladeSchluessel = textartig ? item.id : `${item.id}:${datei?.original.id}`
  useEffect(() => {
    if (!datei || !userKey) return
    const abbruch = new AbortController()
    const id = item.id
    setAnzeige({ id, ...LEER })
    const ganz = art === 'pdf' || art === 'archiv' || art === 'schrift'
    const mitInhalt =
      art === 'bild' || art === 'video' || art === 'audio' || (textartig && groesse <= TEXT_HOECHSTENS) || (ganz && groesse <= GANZ_HOECHSTENS)
    if (!mitInhalt) {
      setAnzeige({ id, ...LEER, anteil: null })
      return () => abbruch.abort()
    }
    let url: string | null = null
    void (async () => {
      try {
        const blob = await blobLesen(datei.original, id, userKey, datei.typ, {
          zuletzt: true,
          signal: abbruch.signal,
          fortschritt: (anteil) => setAnzeige((a) => (a.id === id ? { ...a, anteil } : a)),
        })
        // Während des Ladens gesperrt oder weitergeblättert: nichts mehr anzeigen.
        if (abbruch.signal.aborted || useVaultStore.getState().userKey !== userKey) return
        const fertig = { id, ...LEER, anteil: null }
        if (textartig) {
          const bytes = new Uint8Array(await blob.arrayBuffer())
          // Eine unbekannte Datei, die kein Text ist, bekommt die Karte.
          setAnzeige(art !== null || istText(bytes) ? { ...fertig, text: new TextDecoder().decode(bytes) } : fertig)
        } else if (ganz) {
          const bytes = new Uint8Array(await blob.arrayBuffer())
          if (art === 'archiv') {
            const archiv = await archivInhalt(bytes).catch(() => null)
            if (abbruch.signal.aborted) return
            setAnzeige(archiv ? { ...fertig, archiv } : { ...fertig, nichtDarstellbar: true })
          } else {
            setAnzeige({ ...fertig, bytes })
          }
        } else {
          url = ansichtOeffnen(blob)
          setAnzeige({ ...fertig, url })
        }
      } catch {
        const offline = typeof navigator !== 'undefined' && navigator.onLine === false
        if (!abbruch.signal.aborted) setAnzeige((a) => (a.id === id ? { ...a, anteil: null, fehler: true, offline } : a))
      }
    })()
    return () => {
      abbruch.abort()
      if (url) ansichtSchliessen(url)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ladeSchluessel, userKey, versuch])

  // Ohne Netz gescheitert: sobald es zurück ist, von selbst noch einmal.
  const wartetAufNetz = anzeige.fehler && anzeige.offline
  useEffect(() => {
    if (!wartetAufNetz) return
    const los = () => setVersuch((v) => v + 1)
    window.addEventListener('online', los)
    return () => window.removeEventListener('online', los)
  }, [wartetAufNetz])

  if (!datei) return null

  const aufGeraet = async () => {
    if (!userKey) return
    try {
      await aufGeraetSpeichern(datei.original, item.id, userKey, item.service, datei.typ)
    } catch {
      toast.error(t('mss.vault.dateien.speichernFehler'))
    }
  }

  const inPapierkorb = async () => {
    try {
      await trashItem(item.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.dateien.papierkorbFehler'))
      return
    }
    toast.success(t('mss.vault.inPapierkorbGelegt'), {
      label: t('common.undo'),
      ausfuehren: () => void restoreItem(item.id).catch(() => toast.error(t('mss.vault.dateien.rueckgaengigFehler'))),
    })
    onSchliessen()
  }

  const zurueckholen = async (id: string) => {
    setHolt(id)
    try {
      await fassungZurueckholen(item.id, id)
      toast.success(t('mss.vault.dateien.fassungZurueck'))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.bearbeiten.fehler'))
    } finally {
      setHolt(null)
    }
  }

  // Textdateien haben keine eigene Ansicht: sobald der Text da ist, geht der Editor auf.
  if (anzeige.id === item.id && anzeige.text !== null) {
    return (
      <TresorTexteditor
        item={item}
        text={anzeige.text}
        ort={ort}
        ansicht={art === 'markdown' || art === 'tabelle' ? art : undefined}
        onFertig={onSchliessen}
      />
    )
  }
  if (bildBearbeiten && anzeige.url) {
    return <TresorBildeditor item={item} vorschauUrl={anzeige.url} onFertig={() => setBildBearbeiten(false)} />
  }

  const datum = (ms: number) => new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(ms)
  const index = folge.findIndex((f) => f.id === item.id)
  const Icon = dateiIcon(datei.typ, item.service)
  const geladen = anzeige.id === item.id
  const nichtDarstellbar = () => setAnzeige((a) => (a.id === item.id ? { ...a, nichtDarstellbar: true } : a))
  const rollbar = geladen && !anzeige.nichtDarstellbar && (anzeige.bytes !== null || anzeige.archiv !== null)

  let inhalt: React.ReactNode
  if (geladen && anzeige.fehler && anzeige.offline) {
    inhalt = (
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center" role="status">
        <WifiOff className="h-10 w-10 text-white/60" aria-hidden />
        <p className="font-semibold text-white">{t('mss.vault.dateien.ohneNetzTitel')}</p>
        <p className="text-sm text-white/70">{t('mss.vault.dateien.ohneNetzHinweis')}</p>
      </div>
    )
  } else if (geladen && anzeige.fehler) {
    inhalt = (
      <div className="flex max-w-sm flex-col items-center gap-4 px-6 text-center" role="alert">
        <p className="text-sm text-white/70">{t('mss.vault.dateien.oeffnenFehler')}</p>
        <Button type="button" variant="secondary" className="min-h-11 sm:min-h-10" onClick={() => setVersuch((v) => v + 1)}>
          <RotateCw className="h-4 w-4" aria-hidden />
          {t('mss.vault.dateien.erneutLaden')}
        </Button>
      </div>
    )
  } else if (geladen && anzeige.url && art === 'bild' && !anzeige.nichtDarstellbar) {
    inhalt = <img src={anzeige.url} alt={item.service} draggable={false} onError={nichtDarstellbar} className="max-h-full max-w-full object-contain" />
  } else if (geladen && anzeige.url && art === 'video' && !anzeige.nichtDarstellbar) {
    inhalt = <video src={anzeige.url} controls onError={nichtDarstellbar} className="max-h-full max-w-full" />
  } else if (rollbar && anzeige.archiv) {
    inhalt = <Archivliste eintraege={anzeige.archiv} label={t('mss.vault.dateien.archiv.inhalt', { name: item.service })} />
  } else if (rollbar && anzeige.bytes && art === 'pdf') {
    inhalt = (
      <Suspense fallback={null}>
        <PdfAnsicht daten={anzeige.bytes} label={item.service} aufDunkel />
      </Suspense>
    )
  } else if (rollbar && anzeige.bytes && art === 'schrift') {
    inhalt = <Schriftprobe daten={anzeige.bytes} />
  } else if (geladen && anzeige.anteil !== null) {
    inhalt = (
      <div className="w-64 rounded-lg bg-black/60 p-3">
        <ProgressBar value={anzeige.anteil * 100} label={t('mss.vault.dateien.wirdEntschluesselt')} />
      </div>
    )
  } else {
    // Ohne Vorschau (oder Audio): eine ruhige Karte mit dem, was man tun kann.
    inhalt = (
      <div className="flex w-full max-w-sm flex-col items-center gap-4 rounded-2xl border border-white/10 bg-white/[0.04] px-6 py-8 text-center">
        <Icon className="h-14 w-14 text-white/60" aria-hidden />
        <div className="min-w-0 max-w-full">
          <p className="break-words font-semibold text-white">{item.service}</p>
          <p className="mt-0.5 text-label-sm text-white/55">
            {t(`mss.vault.dateien.art.${artBezeichnung(datei.typ, item.service)}`)} · {formatBytes(datei.original.echt)}
          </p>
        </div>
        {art === 'audio' && anzeige.url && !anzeige.nichtDarstellbar ? (
          <audio src={anzeige.url} controls onError={nichtDarstellbar} className="w-full" />
        ) : (
          <>
            <p className="text-sm text-white/70">
              {t(anzeige.nichtDarstellbar ? 'mss.vault.dateien.formatNichtDarstellbar' : 'mss.vault.dateien.keineVorschau')}
            </p>
            <Button type="button" onClick={() => void aufGeraet()}>
              <Download className="mr-1.5 h-4 w-4" />
              {t('mss.vault.dateien.speichern')}
            </Button>
          </>
        )}
      </div>
    )
  }

  const info = (
    <div className="space-y-6">
      <dl className="space-y-3">
        {[
          [t('mss.vault.fotos.info.name'), item.service],
          [t('mss.vault.dateien.ort'), ort],
          [t('mss.vault.fotos.info.groesse'), formatBytes(datei.original.echt)],
          [t('mss.vault.dateien.geaendert'), datum(datei.geaendert ?? item.updatedAt)],
          [t('mss.vault.fotos.info.hinzugefuegt'), datum(item.createdAt)],
        ].map(([name, wert]) => (
          <div key={name}>
            <dt className="text-label-sm text-white/55">{name}</dt>
            <dd className="break-words text-white/90">{wert}</dd>
          </div>
        ))}
      </dl>
      <section aria-label={t('common.versionen.titel')}>
        <h3 className="mb-1 text-sm font-semibold text-white">{t('common.versionen.titel')}</h3>
        {fassungenVon(item).length > 0 && <p className="mb-3 text-label-sm text-white/55">{t('common.versionen.hinweis')}</p>}
        <Versionsliste versionen={fassungenVon(item)} onWiederherstellen={(id) => void zurueckholen(id)} laeuft={holt} aufDunkel />
      </section>
    </div>
  )

  return (
    <Lichtbox
      kennung={item.id}
      titel={item.service}
      untertitel={formatBytes(datei.original.echt)}
      position={index >= 0 && folge.length > 1 ? { index, anzahl: folge.length } : undefined}
      onSchliessen={onSchliessen}
      onVor={index >= 0 && index < folge.length - 1 ? () => onWechseln(folge[index + 1]) : undefined}
      onZurueck={index > 0 ? () => onWechseln(folge[index - 1]) : undefined}
      zoombar={art === 'bild'}
      rollbar={rollbar}
      info={info}
      aktionen={
        <>
          {art === 'bild' && BEARBEITBAR.includes(datei.typ) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={KNOPF}
              aria-label={t('mss.vault.bearbeiten.knopf')}
              disabled={!anzeige.url}
              onClick={() => setBildBearbeiten(true)}
            >
              <Crop className="h-4 w-4" />
              <span className="ml-1.5 hidden sm:inline">{t('mss.vault.bearbeiten.knopf')}</span>
            </Button>
          )}
          <Button type="button" variant="ghost" size="sm" className={KNOPF} aria-label={t('mss.vault.dateien.speichern')} onClick={() => void aufGeraet()}>
            <Download className="h-4 w-4" />
            <span className="ml-1.5 hidden sm:inline">{t('mss.vault.dateien.speichern')}</span>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={KNOPF}
            aria-label={t('mss.vault.inPapierkorb')}
            onClick={() => void inPapierkorb()}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </>
      }
    >
      {inhalt}
    </Lichtbox>
  )
}
