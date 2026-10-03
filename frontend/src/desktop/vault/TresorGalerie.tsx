/**
 * Der Bereich „Fotos“ im Tresor: alle Bilder und Videos als Raster, nach
 * Monaten gruppiert, mit Lichtbox.
 *
 * Das Raster zeigt nur Miniaturen (256 px, WebP), die Lichtbox die Vorschau
 * (1600 px) und erst beim Hineinzoomen oder Abspielen das Original. Alles
 * wird auf dem Gerät entschlüsselt; Monatsgruppen außerhalb des Bildschirms
 * überspringt der Browser (`content-visibility`), und Miniaturen werden erst
 * geladen, wenn ihre Kachel in die Nähe kommt.
 *
 * Mehrfachauswahl wie in „Dateien“: am Rechner mit Strg/Cmd- und
 * Umschalt-Klick oder „Auswählen“, am Telefon mit langem Drücken. Die
 * Aktionen stehen am Rechner oben, am Telefon unten in Daumenreichweite.
 */

import React, { createContext, memo, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ArrowLeft,
  CheckSquare,
  Clock,
  Copy,
  Crop,
  Download,
  Film,
  FolderInput,
  FolderMinus,
  HardDriveDownload,
  ImageIcon,
  Images,
  Library,
  Minus,
  Pencil,
  Play,
  Plus,
  Trash2,
  Upload,
  Check,
  type LucideIcon,
} from 'lucide-react'
import {
  Ablageflaeche,
  ActionMenu,
  Auswahlleiste,
  Button,
  Dropdown,
  FileButton,
  Kontextmenue,
  Lichtbox,
  ProgressBar,
  Zustandsflaeche,
  buttonClasses,
  useMehrfachauswahl,
  type ActionMenuItem,
  type AuswahlAktion,
  type Auswahltasten,
  type DropdownOption,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes, formatDauer, formatZeitpunkt } from '@/lib/format'
import { useLangdruck } from '@/hooks/useLangdruck'
import { ZipZuGross } from '@/lib/zipSchreiben'
import { cx } from '@/utils/classNames'
import { useVaultStore } from './vaultStore'
import { type VaultItem } from './vaultEintrag'
import { ansichtOeffnen, ansichtSchliessen, blobLesen, useTresorUploads, angeheftet, offlineAnheften, offlineLoesen } from './tresorDateien'
import { miniaturenVorladen, useMiniatur } from './tresorMiniaturen'
import { dateiAufsGeraet, mehrereAufGeraetSpeichern } from './tresorAnzeige'
import { gruppieren, hashesBerechnen } from './tresorAehnlich'
import { BEARBEITBAR, TresorBildeditor } from './TresorBildeditor'
import { dateienUnter, sichtbareEintraege } from './tresorOrdner'
import { fehlerText } from './tresorFehler'
import { inDenPapierkorb, insArchiv, zuruecknehmen, type SammelStand } from './tresorSammel'
import { LadeFehlerHinweis, TresorDateiInfo, ladeFehler, useNeuBeiNetz, type LadeFehler } from './TresorLichtboxTeile'
import { TresorSpeicherAnzeige, TresorUploadStand, useTresorSpeicher } from './TresorSpeicher'

type Filter = 'alle' | 'videos' | 'kuerzlich' | 'aehnlich' | 'alben'

/** Kantenlänge der Kacheln je Zoomstufe. */
const KACHEL = [84, 128, 200] as const

/** Am Telefon mindestens 44 px; `min-h` setzt sich gegen die Höhe aus `size` durch. */
const DAUMEN = 'min-h-11 sm:min-h-0'
const DAUMEN_QUADRAT = 'min-h-11 min-w-11 sm:min-h-0 sm:min-w-0'

const SCREENSHOT = /screenshot|bildschirmfoto|screen[ _-]?shot|scrnshot|capture d.?écran/i

export function istMedium(item: VaultItem): boolean {
  const typ = item.datei?.typ ?? ''
  return item.category === 'datei' && (typ.startsWith('image/') || typ.startsWith('video/'))
}

function istVideo(item: VaultItem): boolean {
  return !!item.datei?.typ.startsWith('video/')
}

const WHATSAPP = /^(IMG|VID)-\d{8}-WA\d+|whatsapp/i
const KI_BILD = /dall[·.\s_-]?e|chatgpt|midjourney|stable[ _-]?diffusion|gemini_generated|firefly|ai[_-]generated/i
const KAMERA_NAME = /^(IMG|VID|PXL|DSC|DSCN|DCIM|MVIMG|MOV|GOPR|DJI)[_-]/i

export type QuellenArt = 'kamera' | 'screenshot' | 'whatsapp' | 'ki' | 'sonstige'

/**
 * Woher ein Bild vermutlich stammt, aus Dateiname und EXIF abgeleitet. Das
 * steht nirgends gespeichert; es ist eine Vermutung für den Filter.
 */
export function quelleVon(item: VaultItem): { art: QuellenArt; modell?: string } {
  const name = item.service
  if (!istVideo(item) && SCREENSHOT.test(name)) return { art: 'screenshot' }
  if (WHATSAPP.test(name)) return { art: 'whatsapp' }
  if (KI_BILD.test(name)) return { art: 'ki' }
  if (item.datei?.kamera) return { art: 'kamera', modell: item.datei.kamera }
  if (KAMERA_NAME.test(name)) return { art: 'kamera' }
  return { art: 'sonstige' }
}

/** Passt ein Bild zur gewählten Quelle? `kamera:<Modell>` wählt ein Modell. */
function quellePasst(item: VaultItem, quelle: string): boolean {
  if (!quelle) return true
  const q = quelleVon(item)
  if (quelle.startsWith('kamera:')) return q.modell === quelle.slice(7)
  return q.art === quelle
}

/**
 * Wann ein Bild entstand. Die EXIF-Zeit ist Ortszeit der Kamera ohne Zone und
 * wird deshalb als UTC gelesen und angezeigt; alles andere ist ein echter
 * Zeitpunkt in Ortszeit.
 */
function zeitpunkt(item: VaultItem, filter: Filter): { ms: number; utc: boolean } {
  if (filter !== 'kuerzlich') {
    if (item.datei?.aufgenommen) return { ms: item.datei.aufgenommen, utc: true }
    if (item.datei?.geaendert) return { ms: item.datei.geaendert, utc: false }
  }
  return { ms: item.createdAt, utc: false }
}

/**
 * Fotos, die an diesem Kalendertag in früheren Jahren entstanden, je Jahr eine
 * Gruppe, das jüngste Jahr zuerst. Zählt nur das Aufnahmedatum (oder die
 * Änderung auf dem Gerät), nie das Hochladen: sonst wäre jedes vor einem Jahr
 * hochgeladene Foto eine Erinnerung.
 */
export function anDiesemTag(medien: VaultItem[], jetzt: Date): { jahre: number; items: VaultItem[] }[] {
  const nachJahren = new Map<number, VaultItem[]>()
  for (const item of medien) {
    const aufgenommen = item.datei?.aufgenommen
    const ms = aufgenommen ?? item.datei?.geaendert
    if (!ms) continue
    const d = new Date(ms)
    // EXIF-Zeit ist Wanduhrzeit, als UTC gespeichert (siehe `zeitpunkt`).
    const [jahr, monat, tag] = aufgenommen ? [d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()] : [d.getFullYear(), d.getMonth(), d.getDate()]
    const jahre = jetzt.getFullYear() - jahr
    if (jahre < 1 || monat !== jetzt.getMonth() || tag !== jetzt.getDate()) continue
    nachJahren.set(jahre, [...(nachJahren.get(jahre) ?? []), item])
  }
  return [...nachJahren.entries()].sort(([a], [b]) => a - b).map(([jahre, items]) => ({ jahre, items }))
}

/** Ein gemeinsamer IntersectionObserver für alle Kacheln, am Scrollbereich. */
type Beobachten = (el: Element, melden: (sichtbar: boolean) => void) => () => void
const SichtbarKontext = createContext<Beobachten | null>(null)

function useBeobachter(wurzel: React.RefObject<HTMLElement | null>): Beobachten | null {
  const [beobachten, setBeobachten] = useState<Beobachten | null>(null)
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined' || !wurzel.current) return
    const melder = new Map<Element, (sichtbar: boolean) => void>()
    const io = new IntersectionObserver(
      (eintraege) => {
        for (const e of eintraege) melder.get(e.target)?.(e.isIntersecting)
      },
      { root: wurzel.current, rootMargin: '600px 0px' },
    )
    setBeobachten(() => (el: Element, melden: (sichtbar: boolean) => void) => {
      melder.set(el, melden)
      io.observe(el)
      return () => {
        io.unobserve(el)
        melder.delete(el)
      }
    })
    return () => io.disconnect()
  }, [wurzel])
  return beobachten
}

function useSichtbar(ref: React.RefObject<HTMLElement | null>): boolean {
  const beobachten = useContext(SichtbarKontext)
  // Ohne Beobachter (Tests, alte WebViews) gilt alles als sichtbar.
  const [sichtbar, setSichtbar] = useState(typeof IntersectionObserver === 'undefined')
  useEffect(() => {
    if (!beobachten || !ref.current) return
    return beobachten(ref.current, setSichtbar)
  }, [beobachten, ref])
  return sichtbar
}

/**
 * Die Kachel, zu der eine Pfeiltaste im Raster führt. Links und rechts gehen
 * in Leserichtung (auch über Monatsgrenzen), hoch und runter in die nächste
 * Zeile zur Kachel, die der Mitte am nächsten liegt. Gemessen wird nur bis zum
 * Ende dieser Zeile, nicht über alle Kacheln.
 */
export function nachbarKachel(raster: HTMLElement, von: HTMLElement, taste: string): HTMLElement | null {
  const alle = [...raster.querySelectorAll<HTMLElement>('[data-kachel]')]
  const i = alle.indexOf(von)
  if (i < 0) return null
  if (taste === 'ArrowLeft') return alle[i - 1] ?? null
  if (taste === 'ArrowRight') return alle[i + 1] ?? null
  if (taste === 'Home') return alle[0] ?? null
  if (taste === 'End') return alle[alle.length - 1] ?? null
  if (taste !== 'ArrowUp' && taste !== 'ArrowDown') return null
  const runter = taste === 'ArrowDown'
  const a = von.getBoundingClientRect()
  const mitte = a.left + a.width / 2
  let zeile: number | null = null
  let beste: HTMLElement | null = null
  let abstand = Infinity
  for (let j = i + (runter ? 1 : -1); j >= 0 && j < alle.length; j += runter ? 1 : -1) {
    const b = alle[j].getBoundingClientRect()
    if (runter ? b.top < a.bottom - 1 : b.bottom > a.top + 1) continue
    if (zeile === null) zeile = b.top
    else if (Math.abs(b.top - zeile) > 1) break
    const d = Math.abs(b.left + b.width / 2 - mitte)
    if (d < abstand) {
      abstand = d
      beste = alle[j]
    }
  }
  return beste
}

const Kachel = memo(function Kachel({
  item,
  ausgewaehlt,
  offline,
  tabHalt,
  onKlick,
  onLangdruck,
  onMenue,
  onFokus,
}: {
  item: VaultItem
  /** Nur im Auswahlmodus gesetzt. */
  ausgewaehlt?: boolean
  /** Ist das Original offline auf dem Gerät verfügbar? */
  offline?: boolean
  /** Die eine Kachel, die Tab erreicht; die übrigen erreichen die Pfeiltasten. */
  tabHalt: boolean
  onKlick: (id: string, tasten: Auswahltasten) => void
  onLangdruck: (id: string) => void
  /** Rechtsklick und Menütaste; am Finger gehört der lange Druck der Auswahl. */
  onMenue: (id: string, x: number, y: number, ausloeser: HTMLElement) => void
  onFokus: (id: string) => void
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLButtonElement>(null)
  const sichtbar = useSichtbar(ref)
  const url = useMiniatur(item.datei?.miniatur, item.id, sichtbar)
  const upload = useTresorUploads((s) => s.je[item.id])
  const lang = useLangdruck(() => onLangdruck(item.id))
  const beschreibung = useId()
  const video = istVideo(item)
  const Symbol = video ? Film : ImageIcon
  const dauer = item.datei?.dauer ? formatDauer(item.datei.dauer) : null
  return (
    <button
      ref={ref}
      type="button"
      data-kachel={item.id}
      tabIndex={tabHalt ? 0 : -1}
      onFocus={() => onFokus(item.id)}
      onClick={(event) => onKlick(item.id, event)}
      onPointerDown={lang.onPointerDown}
      onPointerMove={lang.onPointerMove}
      onPointerUp={lang.onPointerUp}
      onPointerCancel={lang.onPointerCancel}
      onContextMenu={(event) => {
        lang.onContextMenu(event)
        if (event.defaultPrevented) return
        event.preventDefault()
        onMenue(item.id, event.clientX, event.clientY, event.currentTarget)
      }}
      aria-label={item.service}
      aria-describedby={video ? beschreibung : undefined}
      aria-pressed={ausgewaehlt}
      className={`group relative aspect-square overflow-hidden rounded-md bg-surface-container focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${
        ausgewaehlt ? 'ring-2 ring-primary ring-offset-2 ring-offset-surface' : ''
      }`}
    >
      {url ? (
        <img
          src={url}
          alt=""
          draggable={false}
          className={`h-full w-full object-cover transition-transform ${ausgewaehlt ? 'scale-90 rounded' : 'group-hover:scale-[1.03]'}`}
        />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-on-surface-variant/60">
          <Symbol className="h-6 w-6" />
        </span>
      )}
      {ausgewaehlt !== undefined && (
        <span
          className={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full border-2 ${
            ausgewaehlt ? 'border-primary bg-primary text-on-primary' : 'border-white/80 bg-black/30'
          }`}
        >
          {ausgewaehlt && <Check className="h-3 w-3" />}
        </span>
      )}
      {ausgewaehlt === undefined && offline && (
        <span
          className="absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-secondary"
          role="img"
          aria-label={t('mss.vault.dateien.offlineVerfuegbar')}
        >
          <HardDriveDownload className="h-3 w-3" />
        </span>
      )}
      {video && (
        <span className="absolute bottom-1 right-1 inline-flex items-center gap-0.5 rounded bg-black/60 px-1 text-label-sm font-medium text-white" aria-hidden>
          <Play className="h-2.5 w-2.5 fill-current" />
          {dauer}
        </span>
      )}
      {video && (
        // Der Name bleibt der Dateiname; dass es ein Video ist und wie lang, liest der Screenreader als Beschreibung.
        <span id={beschreibung} className="sr-only">
          {dauer ? t('mss.vault.fotos.videoMitDauer', { dauer }) : t('mss.vault.fotos.video')}
        </span>
      )}
      {upload && (
        <span className="absolute inset-x-1 bottom-1">
          {upload.fehler ? (
            <span className="rounded bg-black/70 px-1 text-label-sm text-status-destructive">{t(`mss.vault.dateien.${upload.fehler}`)}</span>
          ) : (
            <ProgressBar value={upload.gesamt > 0 ? (upload.gesendet / upload.gesamt) * 100 : null} ariaLabel={t('mss.vault.dateien.wirdHochgeladen')} />
          )}
        </span>
      )}
    </button>
  )
})

function AlbumKarte({ album, titelbild, anzahl, onOeffnen }: { album: VaultItem; titelbild?: VaultItem; anzahl: number; onOeffnen: () => void }) {
  const { t } = useTranslation()
  const url = useMiniatur(titelbild?.datei?.miniatur, titelbild?.id ?? '', true)
  return (
    <button type="button" onClick={onOeffnen} className="group text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
      <span className="block aspect-square overflow-hidden rounded-lg bg-surface-container">
        {url ? (
          <img src={url} alt="" draggable={false} className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]" />
        ) : (
          <span className="flex h-full w-full items-center justify-center text-on-surface-variant/60">
            <Images className="h-8 w-8" />
          </span>
        )}
      </span>
      <span className="mt-1.5 block truncate text-xs font-semibold text-on-surface">{album.service}</span>
      <span className="block text-label-sm text-on-surface-variant">{t('mss.vault.fotos.album', { count: anzahl })}</span>
    </button>
  )
}

function ErinnerungKarte({ jahre, titelbild, anzahl, onOeffnen }: { jahre: number; titelbild: VaultItem; anzahl: number; onOeffnen: () => void }) {
  const { t } = useTranslation()
  const url = useMiniatur(titelbild.datei?.miniatur, titelbild.id, true)
  const titel = t('mss.vault.fotos.vorJahren', { count: jahre })
  return (
    <button
      type="button"
      onClick={onOeffnen}
      aria-label={`${titel}, ${t('mss.vault.fotos.album', { count: anzahl })}`}
      className="group relative block aspect-[3/4] w-32 shrink-0 overflow-hidden rounded-lg bg-surface-container text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
    >
      {url ? (
        <img src={url} alt="" draggable={false} className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]" />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-on-surface-variant/60">
          <Images className="h-8 w-8" />
        </span>
      )}
      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-6 text-xs font-semibold text-white" aria-hidden>
        {titel}
      </span>
    </button>
  )
}

const FILTER: { id: Filter; labelKey: string; icon: LucideIcon }[] = [
  { id: 'alle', labelKey: 'mss.vault.alle', icon: Images },
  { id: 'videos', labelKey: 'mss.vault.fotos.videos', icon: Film },
  { id: 'kuerzlich', labelKey: 'mss.vault.fotos.kuerzlich', icon: Clock },
  { id: 'aehnlich', labelKey: 'mss.vault.fotos.aehnlich', icon: Copy },
  { id: 'alben', labelKey: 'mss.vault.fotos.alben', icon: Library },
]

export function TresorGalerie({ suche = '' }: { suche?: string }) {
  const { t, i18n } = useTranslation()
  const items = useVaultStore((s) => s.items)
  const userKey = useVaultStore((s) => s.userKey)
  const dateiHinzufuegen = useVaultStore((s) => s.dateiHinzufuegen)
  const albumAnlegen = useVaultStore((s) => s.albumAnlegen)
  const albumAendern = useVaultStore((s) => s.albumAendern)
  const aendern = useVaultStore((s) => s.aendern)
  const trashItem = useVaultStore((s) => s.trashItem)
  const restoreItem = useVaultStore((s) => s.restoreItem)
  const { speicher, ohneSpeicher } = useTresorSpeicher()
  const [filter, setFilterRoh] = useState<Filter>('alle')
  const [quelle, setQuelle] = useState('')
  const [aehnlich, setAehnlich] = useState<{ fertig: number; gesamt: number; gruppen: string[][] | null }>({ fertig: 0, gesamt: 0, gruppen: null })
  const [albumId, setAlbumId] = useState<string | null>(null)
  /** Eine laufende Sammelaktion, mit Fortschritt. Solange sie läuft, sind die Aktionen gesperrt. */
  const [sammel, setSammel] = useState<SammelStand>(null)
  const [albumMenue, setAlbumMenue] = useState<{ x: number; y: number; ausloeser: HTMLElement | null } | null>(null)
  const [kachelMenue, setKachelMenue] = useState<{ id: string; x: number; y: number; ausloeser: HTMLElement } | null>(null)
  const kachelMenueOeffnen = useCallback(
    (id: string, x: number, y: number, ausloeser: HTMLElement) => setKachelMenue({ id, x, y, ausloeser }),
    [],
  )
  const [stufe, setStufe] = useState(1)
  const [offen, setOffen] = useState<{ id: string; index: number } | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)
  const [ziehen, setZiehen] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const beobachten = useBeobachter(scroller)

  const sichtbar = useMemo(() => sichtbareEintraege(items), [items])
  const medien = useMemo(() => sichtbar.filter(istMedium), [sichtbar])
  const alben = useMemo(
    () => sichtbar.filter((i) => i.category === 'album').sort((a, b) => a.service.localeCompare(b.service)),
    [sichtbar],
  )
  // Ein Album, das inzwischen gelöscht ist (auch auf einem anderen Gerät), ist nicht mehr offen.
  const album = albumId ? alben.find((a) => a.id === albumId) : undefined
  const albumInhalt = useCallback(
    (a: VaultItem) => {
      const nachId = new Map(medien.map((m) => [m.id, m]))
      return (a.album?.eintraege ?? []).map((id) => nachId.get(id)).filter((m): m is VaultItem => !!m)
    },
    [medien],
  )

  // Ähnliche erst suchen, wenn jemand danach fragt: dafür wird jede Miniatur entschlüsselt.
  const anzahlMedien = medien.length
  useEffect(() => {
    if (filter !== 'aehnlich' || !userKey) return
    let abgebrochen = false
    const alle = sichtbareEintraege(useVaultStore.getState().items).filter(istMedium)
    setAehnlich({ fertig: 0, gesamt: alle.length, gruppen: null })
    void hashesBerechnen(alle, userKey, () => abgebrochen, (fertig) => !abgebrochen && setAehnlich((a) => ({ ...a, fertig })))
      .then((hashes) => {
        if (!abgebrochen) setAehnlich({ fertig: alle.length, gesamt: alle.length, gruppen: gruppieren(hashes) })
      })
      .catch(() => !abgebrochen && setAehnlich({ fertig: 0, gesamt: 0, gruppen: [] }))
    return () => {
      abgebrochen = true
    }
  }, [filter, userKey, anzahlMedien])

  const liste = useMemo(() => {
    const wort = suche.trim().toLocaleLowerCase()
    const nachId = new Map(medien.map((m) => [m.id, m]))
    const basis = album
      ? albumInhalt(album)
      : filter === 'alben'
        ? []
        : filter === 'aehnlich'
          ? (aehnlich.gruppen ?? []).flat().map((id) => nachId.get(id)).filter((m): m is VaultItem => !!m)
          : medien
    const auswahlListe = basis.filter(
      (i) =>
        (filter !== 'videos' || istVideo(i)) &&
        quellePasst(i, quelle) &&
        (!wort || i.service.toLocaleLowerCase().includes(wort) || !!i.datei?.kamera?.toLocaleLowerCase().includes(wort)),
    )
    // Ähnliche bleiben in ihren Gruppen beieinander.
    if (filter === 'aehnlich' && !album) return auswahlListe
    return auswahlListe.sort((a, b) => zeitpunkt(b, filter).ms - zeitpunkt(a, filter).ms)
  }, [medien, filter, suche, album, albumInhalt, quelle, aehnlich.gruppen])
  // Nur in „Alle“ ohne Album und Suche; der Tag wechselt beim nächsten Rendern nach Mitternacht.
  const erinnerungen = useMemo(
    () => (filter === 'alle' && !album && !suche.trim() ? anDiesemTag(liste, new Date()) : []),
    [filter, album, suche, liste],
  )
  // Mehrfachauswahl wie in „Dateien“, in der Reihenfolge des Rasters.
  const wahl = useMehrfachauswahl(liste.map((i) => i.id))
  const { auswahl, leeren: auswahlLeeren, klick: auswahlKlick, langdruck: kachelLangdruck } = wahl

  const [offline, setOffline] = useState<Set<string>>(new Set())
  const offlineLaden = useCallback(() => {
    const ids = medien.filter((i) => i.datei).map((i) => i.datei!.original.id)
    if (ids.length === 0) return
    void angeheftet(ids).then(setOffline)
  }, [medien])
  useEffect(() => {
    offlineLaden()
  }, [offlineLaden])

  const offlineUmschalten = async (item: VaultItem) => {
    if (!item.datei || !userKey) return
    const kopf = item.datei.original
    if (offline.has(kopf.id)) {
      await offlineLoesen(kopf.id)
      offlineLaden()
      return
    }
    try {
      await offlineAnheften(kopf, item.id, userKey)
      toast.success(t('mss.vault.dateien.offlineFertig', { name: item.service }))
    } catch {
      toast.error(t('mss.vault.dateien.offlineFehler'))
    } finally {
      offlineLaden()
    }
  }

  const offlineSammel = async (gewaehlteListe: VaultItem[]) => {
    const dateien = gewaehlteListe.filter((i) => i.datei)
    if (dateien.length === 0 || sammel || !userKey) return
    const da = await angeheftet(dateien.map((d) => d.datei!.original.id))
    if (dateien.every((d) => da.has(d.datei!.original.id))) {
      for (const d of dateien) await offlineLoesen(d.datei!.original.id)
      toast.success(t('mss.vault.dateien.nurOnlineMehrere', { count: dateien.length }))
    } else {
      const fehlen = dateien.filter((d) => !da.has(d.datei!.original.id))
      const gesamt = fehlen.reduce((s, d) => s + d.datei!.original.echt, 0) || 1
      let erledigt = 0
      let fehler = 0
      const text = t('mss.vault.dateien.offlineSammelLaedt', { count: fehlen.length })
      setSammel({ text, anteil: 0 })
      for (const d of fehlen) {
        const groesse = d.datei!.original.echt
        try {
          await offlineAnheften(d.datei!.original, d.id, userKey, (anteil) => setSammel({ text, anteil: (erledigt + anteil * groesse) / gesamt }))
        } catch {
          fehler += 1
        }
        erledigt += groesse
      }
      setSammel(null)
      if (fehler === 0) toast.success(t('mss.vault.dateien.offlineFertigMehrere', { count: fehlen.length }))
      else toast.error(t('mss.vault.dateien.offlineTeilweise', { count: fehler, gesamt: fehlen.length }))
    }
    offlineLaden()
    auswahlLeeren()
  }

  const setFilter = (neu: Filter) => {
    setFilterRoh(neu)
    setAlbumId(null)
    auswahlLeeren()
  }

  const albumWechseln = (id: string | null) => {
    setAlbumId(id)
    auswahlLeeren()
  }

  const quellen = useMemo(() => {
    const zahl = new Map<string, number>()
    for (const m of medien) {
      const q = quelleVon(m)
      zahl.set(q.art, (zahl.get(q.art) ?? 0) + 1)
      if (q.modell) zahl.set(`kamera:${q.modell}`, (zahl.get(`kamera:${q.modell}`) ?? 0) + 1)
    }
    const modelle = [...zahl.keys()].filter((k) => k.startsWith('kamera:')).sort((a, b) => zahl.get(b)! - zahl.get(a)!)
    const arten: QuellenArt[] = ['kamera', 'screenshot', 'whatsapp', 'ki', 'sonstige']
    const optionen: DropdownOption[] = [{ value: '', label: t('mss.vault.fotos.quelle.alle') }]
    for (const art of arten) {
      if (!zahl.get(art)) continue
      optionen.push({ value: art, label: t(`mss.vault.fotos.quelle.${art}`), hint: String(zahl.get(art)) })
      if (art === 'kamera') {
        for (const m of modelle) optionen.push({ value: m, label: m.slice(7), hint: String(zahl.get(m)) })
      }
    }
    return optionen
  }, [medien, t])

  const gruppen = useMemo(() => {
    const ergebnis: { schluessel: string; titel: string; items: VaultItem[] }[] = []
    if (filter === 'aehnlich' && !album) {
      const sichtbar = new Map(liste.map((m) => [m.id, m]))
      for (const [nr, g] of (aehnlich.gruppen ?? []).entries()) {
        const drin = g.map((id) => sichtbar.get(id)).filter((m): m is VaultItem => !!m)
        if (drin.length > 1) ergebnis.push({ schluessel: `g${nr}`, titel: t('mss.vault.fotos.aehnlicheGruppe', { count: drin.length }), items: drin })
      }
      return ergebnis
    }
    const monat = (utc: boolean) =>
      new Intl.DateTimeFormat(i18n.language, { month: 'long', year: 'numeric', timeZone: utc ? 'UTC' : undefined })
    const formate = { utc: monat(true), lokal: monat(false) }
    for (const item of liste) {
      const { ms, utc } = zeitpunkt(item, filter)
      const d = new Date(ms)
      const schluessel = utc ? `${d.getUTCFullYear()}-${d.getUTCMonth()}` : `${d.getFullYear()}-${d.getMonth()}`
      const letzte = ergebnis[ergebnis.length - 1]
      if (letzte?.schluessel === schluessel) letzte.items.push(item)
      else ergebnis.push({ schluessel, titel: (utc ? formate.utc : formate.lokal).format(d), items: [item] })
    }
    return ergebnis
  }, [liste, filter, i18n.language, album, aehnlich.gruppen, t])

  // Offline sollen alle Miniaturen da sein: im Hintergrund nachladen, was fehlt.
  useEffect(() => {
    if (!userKey || anzahlMedien === 0) return
    let abgebrochen = false
    const anfragen = useVaultStore
      .getState()
      .items.filter((i) => istMedium(i) && i.datei)
      .map((i) => ({ kopf: i.datei!.miniatur, eintragId: i.id }))
    const zeit = setTimeout(() => void miniaturenVorladen(anfragen, () => abgebrochen).catch(() => {}), 2000)
    return () => {
      abgebrochen = true
      clearTimeout(zeit)
    }
  }, [userKey, anzahlMedien])

  // ── Auswahl ───────────────────────────────────────────────────────────────

  // Was nicht mehr in der Liste steht (gelöscht, weggefiltert), zählt nicht mehr mit.
  const gewaehlt = useMemo(() => (auswahl ? liste.filter((i) => auswahl.has(i.id)) : []), [auswahl, liste])
  const gewaehltIds = gewaehlt.map((i) => i.id)

  const kachelKlick = useCallback(
    (id: string, tasten: Auswahltasten) => {
      if (!auswahlKlick(id, tasten)) setOffen({ id, index: 0 })
    },
    [auswahlKlick],
  )

  // Ein Tab-Halt für das ganze Raster: sonst führt Tab durch jedes Foto einzeln.
  const [fokusId, setFokusId] = useState<string | null>(null)
  const tabHalt = gruppen.some((g) => g.items.some((i) => i.id === fokusId)) ? fokusId : (gruppen[0]?.items[0]?.id ?? null)

  // ── Hochladen ─────────────────────────────────────────────────────────────

  const fehlerZeigen = (err: unknown) => toast.error(fehlerText(err, t('mss.vault.bearbeiten.fehler')))

  const hochladen = async (dateien: File[]) => {
    // Ohne Speicher wiese der Server den Upload ab; die Anzeige oben sagt, warum.
    if (ohneSpeicher) return
    const passend = dateien.filter((d) => d.type.startsWith('image/') || d.type.startsWith('video/'))
    setVorbereitung((n) => n + passend.length)
    const neue: string[] = []
    for (const datei of passend) {
      try {
        neue.push(await dateiHinzufuegen(datei))
      } catch (err) {
        toast.error(fehlerText(err, t('mss.vault.dateien.hochladenFehler')))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
    // Im offenen Album hochgeladen: gehört auch hinein.
    if (album && neue.length > 0) await albumAendern(album.id, { hinzu: neue }).catch(fehlerZeigen)
  }

  // ── Alben ─────────────────────────────────────────────────────────────────

  const neuesAlbum = async (eintraege: string[]) => {
    const name = await prompt({ message: t('mss.vault.fotos.albumName'), confirmText: t('mss.vault.fotos.neuesAlbum') })
    if (!name?.trim()) return
    try {
      const id = await albumAnlegen(name.trim(), eintraege)
      auswahlLeeren()
      if (eintraege.length === 0) {
        setFilterRoh('alben')
        setAlbumId(id)
      } else {
        toast.success(t('mss.vault.fotos.hinzugefuegt', { name: name.trim() }))
      }
    } catch (err) {
      fehlerZeigen(err)
    }
  }

  const zuAlbum = async (a: VaultItem, ids: string[]) => {
    try {
      await albumAendern(a.id, { hinzu: ids })
      auswahlLeeren()
      toast.success(t('mss.vault.fotos.hinzugefuegt', { name: a.service }))
    } catch (err) {
      fehlerZeigen(err)
    }
  }

  const ausAlbum = async (ids: string[]) => {
    if (!album) return
    try {
      await albumAendern(album.id, { weg: ids })
      auswahlLeeren()
    } catch (err) {
      fehlerZeigen(err)
    }
  }

  const albumUmbenennen = async (a: VaultItem) => {
    const name = await prompt({ message: t('mss.vault.dateien.neuerName'), defaultValue: a.service })
    if (!name?.trim() || name.trim() === a.service) return
    await aendern(a.id, { service: name.trim() }).catch((err) => toast.error(fehlerText(err, t('mss.vault.dateien.umbenennenFehler'))))
  }

  /** Nur das Album geht in den Papierkorb; die Fotos darin bleiben. */
  const albumInPapierkorb = async (a: VaultItem) => {
    try {
      await trashItem(a.id)
    } catch {
      toast.error(t('mss.vault.fotos.fehler.albumPapierkorb'))
      return
    }
    setAlbumId(null)
    toast.success(t('mss.vault.fotos.albumImPapierkorb'), {
      label: t('common.undo'),
      ausfuehren: () => void zuruecknehmen([a.id], restoreItem),
    })
  }

  /** Öffnet die Albenwahl am gerade gedrückten Knopf (oben oder in der Fußleiste). */
  const albumMenueOeffnen = () => {
    const knopf = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null
    const box = knopf?.getBoundingClientRect()
    setAlbumMenue(
      box && box.width > 0
        ? { x: box.left, y: box.bottom + 4, ausloeser: knopf }
        : { x: window.innerWidth / 2, y: window.innerHeight - 96, ausloeser: knopf },
    )
  }

  const albumMenueEintraege = (ids: string[]): ActionMenuItem[] => [
    ...alben
      .filter((a) => a.id !== album?.id)
      .map((a) => ({ key: a.id, label: a.service, icon: <Images className="h-4 w-4" />, onSelect: () => void zuAlbum(a, ids) })),
    {
      key: 'neu',
      label: t('mss.vault.fotos.neuesAlbum'),
      icon: <Plus className="h-4 w-4" />,
      separatorBefore: alben.some((a) => a.id !== album?.id),
      onSelect: () => void neuesAlbum(ids),
    },
  ]

  // ── Sammelaktionen ────────────────────────────────────────────────────────

  // Ein Fehler hält den Rest nicht auf; die Meldung nennt, wie viele nicht gingen (tresorSammel).
  const inPapierkorb = async (ids: string[]) => {
    if (ids.length === 0 || sammel) return
    await inDenPapierkorb(ids, setSammel)
    auswahlLeeren()
  }

  const archivieren = async (ids: string[]) => {
    if (ids.length === 0 || sammel) return
    await insArchiv(ids, setSammel)
    auswahlLeeren()
  }

  /** Ein Foto wie es ist, mehrere als ZIP. */
  const speichernSammel = async (gewaehlteItems: VaultItem[]) => {
    if (!userKey || gewaehlteItems.length === 0 || sammel) return
    if (gewaehlteItems.length === 1) {
      await dateiAufsGeraet(gewaehlteItems[0], userKey)
      auswahlLeeren()
      return
    }
    const dateien = dateienUnter(gewaehlteItems, sichtbar)
    setSammel({ text: t('mss.vault.dateien.zipLaeuft', { count: dateien.length }), anteil: null })
    try {
      await mehrereAufGeraetSpeichern(
        dateien.map((d) => ({ kopf: d.item.datei!.original, eintragId: d.item.id, pfad: d.pfad, geaendert: d.item.datei!.geaendert ?? d.item.updatedAt })),
        userKey,
        `${album?.service ?? t('mss.vault.fotos.zipName')}.zip`,
      )
      auswahlLeeren()
    } catch (err) {
      toast.error(
        err instanceof ZipZuGross
          ? t(err.grund === 'dateien' ? 'mss.vault.dateien.zipZuVieleDateien' : 'mss.vault.dateien.zipZuGross')
          : t('mss.vault.dateien.speichernFehler'),
      )
    } finally {
      setSammel(null)
    }
  }

  const leer = gewaehlt.length === 0
  const beschaeftigt = sammel !== null

  /** Das Menü einer Kachel: dieselben Aktionen wie in der Lichtbox und im Dateibereich. */
  const kachelEintraege = (item: VaultItem): ActionMenuItem[] => {
    const istOffline = Boolean(item.datei && offline.has(item.datei.original.id))
    return [
      { key: 'oeffnen', label: t('mss.vault.dateien.oeffnen'), onSelect: () => setOffen({ id: item.id, index: 0 }) },
      {
        key: 'offline',
        label: t(istOffline ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen'),
        icon: <HardDriveDownload className="h-4 w-4" />,
        disabled: beschaeftigt,
        onSelect: () => void offlineUmschalten(item),
      },
      {
        key: 'speichern',
        label: t('mss.vault.dateien.speichern'),
        icon: <Download className="h-4 w-4" />,
        disabled: beschaeftigt || !userKey,
        onSelect: () => void (userKey && dateiAufsGeraet(item, userKey)),
      },
      { key: 'auswaehlen', label: t('mss.vault.dateien.auswaehlen'), icon: <CheckSquare className="h-4 w-4" />, onSelect: () => kachelLangdruck(item.id) },
      {
        key: 'archiv',
        label: t('mss.vault.archivieren'),
        icon: <Archive className="h-4 w-4" />,
        separatorBefore: true,
        disabled: beschaeftigt,
        onSelect: () => void archivieren([item.id]),
      },
      {
        key: 'papierkorb',
        label: t('mss.vault.inPapierkorb'),
        icon: <Trash2 className="h-4 w-4" />,
        destructive: true,
        disabled: beschaeftigt,
        onSelect: () => void inPapierkorb([item.id]),
      },
    ]
  }
  const menueItem = kachelMenue ? liste.find((i) => i.id === kachelMenue.id) : undefined
  const aktionen: AuswahlAktion[] = [
    {
      key: 'album',
      label: t('mss.vault.fotos.zuAlbum'),
      kurz: t('mss.vault.fotos.kurz.album'),
      icon: <FolderInput className="h-5 w-5 md:h-4 md:w-4" />,
      disabled: leer || beschaeftigt,
      onSelect: albumMenueOeffnen,
    },
    ...(album
      ? [
          {
            key: 'ausAlbum',
            label: t('mss.vault.fotos.ausAlbum'),
            kurz: t('mss.vault.fotos.kurz.ausAlbum'),
            icon: <FolderMinus className="h-5 w-5 md:h-4 md:w-4" />,
            disabled: leer || beschaeftigt,
            onSelect: () => void ausAlbum(gewaehltIds),
          },
        ]
      : []),
    {
      key: 'offline',
      label: t(gewaehlt.length > 0 && gewaehlt.every((i) => i.datei && offline.has(i.datei.original.id)) ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen'),
      kurz: t(gewaehlt.length > 0 && gewaehlt.every((i) => i.datei && offline.has(i.datei.original.id)) ? 'mss.vault.dateien.kurz.nurOnline' : 'mss.vault.dateien.kurz.offline'),
      icon: <HardDriveDownload className="h-5 w-5 md:h-4 md:w-4" />,
      disabled: leer || beschaeftigt,
      onSelect: () => void offlineSammel(gewaehlt),
    },
    {
      key: 'speichern',
      label: t(gewaehlt.length > 1 ? 'mss.vault.dateien.alsZip' : 'mss.vault.dateien.speichern'),
      kurz: t('mss.vault.dateien.kurz.speichern'),
      icon: <Download className="h-5 w-5 md:h-4 md:w-4" />,
      disabled: leer || beschaeftigt,
      onSelect: () => void speichernSammel(gewaehlt),
    },
    {
      key: 'archiv',
      label: t('mss.vault.archivieren'),
      icon: <Archive className="h-5 w-5 md:h-4 md:w-4" />,
      disabled: leer || beschaeftigt,
      onSelect: () => void archivieren(gewaehltIds),
    },
    {
      key: 'papierkorb',
      label: t('mss.vault.inPapierkorb'),
      kurz: t('mss.vault.dateien.kurz.papierkorb'),
      icon: <Trash2 className="h-5 w-5 md:h-4 md:w-4" />,
      destructive: true,
      disabled: leer || beschaeftigt,
      onSelect: () => void inPapierkorb(gewaehltIds),
    },
  ]

  // ── Lichtbox ──────────────────────────────────────────────────────────────

  // Die Lichtbox hält sich an die Kennung; fällt das Bild aus der Liste
  // (Archiv, Papierkorb), zeigt sie das an seiner Stelle.
  let offenIndex = -1
  if (offen) {
    offenIndex = liste.findIndex((i) => i.id === offen.id)
    if (offenIndex < 0 && liste.length > 0) offenIndex = Math.min(offen.index, liste.length - 1)
  }
  const offenItem = offenIndex >= 0 ? liste[offenIndex] : null
  useEffect(() => {
    if (offen && offenItem && (offen.id !== offenItem.id || offen.index !== offenIndex)) setOffen({ id: offenItem.id, index: offenIndex })
    if (offen && !offenItem) setOffen(null)
  }, [offen, offenItem, offenIndex])

  // ── Darstellung ───────────────────────────────────────────────────────────

  const kante = KACHEL[stufe]
  const albenUebersicht = filter === 'alben' && !album
  const auswahlLabel = t('mss.vault.dateien.ausgewaehlt', { count: gewaehlt.length })

  const rasterTaste = (event: React.KeyboardEvent) => {
    const kachel = (event.target as HTMLElement).closest<HTMLElement>('[data-kachel]')
    if (kachel && scroller.current && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      const ziel = nachbarKachel(scroller.current, kachel, event.key)
      if (ziel) {
        event.preventDefault()
        ziel.focus()
        ziel.scrollIntoView?.({ block: 'nearest' })
        return
      }
    }
    // Strg+A und Escape; in der Albenübersicht ist die Liste leer.
    if (wahl.taste(event)) return
    if (event.key === 'Delete' && gewaehlt.length > 0) {
      event.preventDefault()
      void inPapierkorb(gewaehltIds)
    }
  }

  const werkzeuge = (
    <div className="flex items-center justify-end gap-1">
      {!albenUebersicht && (
        <>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={DAUMEN_QUADRAT}
            aria-label={t('mss.vault.fotos.kleiner')}
            disabled={stufe === 0}
            onClick={() => setStufe((s) => Math.max(0, s - 1))}
          >
            <Minus className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={DAUMEN_QUADRAT}
            aria-label={t('mss.vault.fotos.groesser')}
            disabled={stufe === KACHEL.length - 1}
            onClick={() => setStufe((s) => Math.min(KACHEL.length - 1, s + 1))}
          >
            <Plus className="h-4 w-4" />
          </Button>
          <Button type="button" variant="ghost" size="sm" className={DAUMEN} disabled={liste.length === 0} onClick={wahl.starten}>
            <CheckSquare className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.dateien.auswaehlen')}
          </Button>
        </>
      )}
      {albenUebersicht ? (
        <Button type="button" variant="primary" size="sm" className={DAUMEN} onClick={() => void neuesAlbum([])}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          {t('mss.vault.fotos.neuesAlbum')}
        </Button>
      ) : (
        <FileButton multiple accept="image/*,video/*" size="sm" variant="primary" className={DAUMEN} disabled={ohneSpeicher} onFiles={(dateien) => void hochladen(dateien)}>
          <Upload className="mr-1 h-3.5 w-3.5" />
          {t('mss.vault.dateien.hochladen')}
        </FileButton>
      )}
    </div>
  )

  // Filter als Chips: eine Ebene unter den Reitern des Tresors, nicht eine zweite Reiterleiste.
  // Am Telefon eine Zeile zum Wischen, ab sm wie gehabt mit Umbruch.
  const filterLeiste = (
    // -my-1/py-1: der Fokusring wird in der Wischleiste sonst oben und unten abgeschnitten.
    <div className="-mx-4 -my-1 flex items-center gap-2 overflow-x-auto px-4 py-1 msm-ohne-rollbalken sm:mx-0 sm:my-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:py-0">
      <div role="tablist" aria-label={t('mss.vault.fotos.filter')} className="flex shrink-0 items-center gap-1.5 sm:flex-wrap">
        {FILTER.map(({ id, labelKey, icon: Icon }) => {
          const aktiv = filter === id
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={aktiv}
              onClick={() => setFilter(id)}
              className={cx(
                buttonClasses('secondary', 'sm', 'shrink-0 gap-1.5 rounded-full px-3'),
                DAUMEN,
                aktiv && 'border-primary/50 bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary',
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {t(labelKey)}
            </button>
          )
        })}
      </div>
      {!albenUebersicht && quellen.length > 2 && (
        <div className="w-44 shrink-0 sm:w-48">
          <Dropdown value={quelle} onChange={setQuelle} options={quellen} aria-label={t('mss.vault.fotos.quelle.titel')} />
        </div>
      )}
    </div>
  )

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
        // Auch wo nichts angenommen wird, abfangen: sonst öffnet der Browser die Datei.
        e.preventDefault()
        if (albenUebersicht || ohneSpeicher) {
          e.dataTransfer.dropEffect = 'none'
          return
        }
        setZiehen(true)
      }}
      // Beim Wechsel auf ein Kind kommt `dragleave` auch; erst das Verlassen der Fläche zählt.
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setZiehen(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setZiehen(false)
        if (albenUebersicht || ohneSpeicher) return
        const dateien = Array.from(e.dataTransfer.files)
        if (dateien.length > 0) void hochladen(dateien)
      }}
    >
      <div className="shrink-0 space-y-2 px-4 pb-2 pt-3">
        {auswahl ? (
          <div className="flex items-center rounded-lg bg-primary/5 px-2 py-1">
            <Auswahlleiste
              variante="kopf"
              anzahlLabel={auswahlLabel}
              aktionen={aktionen}
              abbrechenLabel={t('mss.vault.dateien.auswahlBeenden')}
              onAbbrechen={auswahlLeeren}
              alleLabel={gewaehlt.length < liste.length ? t('mss.vault.alle') : undefined}
              onAlle={wahl.alle}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            {filterLeiste}
            {werkzeuge}
          </div>
        )}

        {album && (
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" size="icon" className={DAUMEN_QUADRAT} aria-label={t('mss.vault.fotos.alleAlben')} onClick={() => albumWechseln(null)}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-on-surface">{album.service}</h3>
            <ActionMenu
              compact
              align="end"
              label={t('mss.vault.dateien.aktionen')}
              items={[
                { key: 'umbenennen', label: t('mss.vault.dateien.umbenennen'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: () => void albumUmbenennen(album) },
                {
                  key: 'papierkorb',
                  label: t('mss.vault.fotos.albumLoeschen'),
                  icon: <Trash2 className="h-3.5 w-3.5" />,
                  destructive: true,
                  separatorBefore: true,
                  onSelect: () => void albumInPapierkorb(album),
                },
              ]}
            />
          </div>
        )}

        <TresorUploadStand />
        {vorbereitung > 0 && (
          <p role="status" className="text-label-sm text-on-surface-variant">
            {t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}
          </p>
        )}
        {sammel && (
          <div role="status" className="rounded-lg border border-outline-variant/20 bg-surface-container-low px-3 py-2">
            <ProgressBar value={sammel.anteil === null ? null : sammel.anteil * 100} label={sammel.text} />
          </div>
        )}
        {!auswahl && speicher && (
          <div className="sm:max-w-xs">
            <TresorSpeicherAnzeige speicher={speicher} />
          </div>
        )}
      </div>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 pb-3" onKeyDown={rasterTaste}>
        {filter === 'aehnlich' && !album && aehnlich.gruppen === null ? (
          <div className="mx-auto mt-6 w-72">
            <ProgressBar
              value={aehnlich.gesamt > 0 ? (aehnlich.fertig / aehnlich.gesamt) * 100 : null}
              label={t('mss.vault.fotos.aehnlicheSuche')}
              hint={`${aehnlich.fertig} / ${aehnlich.gesamt}`}
            />
          </div>
        ) : albenUebersicht ? (
          alben.length === 0 ? (
            <Zustandsflaeche art="leer" icon={<Library className="h-10 w-10" />} text={t('mss.vault.fotos.keineAlben')} />
          ) : (
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
              {alben.map((a) => {
                const inhalt = albumInhalt(a)
                return <AlbumKarte key={a.id} album={a} titelbild={inhalt[0]} anzahl={inhalt.length} onOeffnen={() => albumWechseln(a.id)} />
              })}
            </div>
          )
        ) : liste.length === 0 ? (
          <Zustandsflaeche
            art="leer"
            icon={<Images className="h-10 w-10" />}
            text={t(
              album
                ? 'mss.vault.fotos.albumLeer'
                : filter === 'aehnlich'
                  ? 'mss.vault.fotos.keineAehnlichen'
                  : filter === 'alle' && !suche.trim()
                    ? ohneSpeicher
                      ? 'mss.vault.fotos.leerOhneSpeicher'
                      : 'mss.vault.fotos.leer'
                    : 'mss.vault.fotos.leerFilter',
            )}
          />
        ) : (
          <SichtbarKontext.Provider value={beobachten}>
            {erinnerungen.length > 0 && !auswahl && (
              <section aria-label={t('mss.vault.fotos.anDiesemTag')} className="mb-4">
                <h3 className="mb-1.5 text-xs font-semibold text-on-surface">{t('mss.vault.fotos.anDiesemTag')}</h3>
                <div className="msm-ohne-rollbalken flex gap-2 overflow-x-auto">
                  {erinnerungen.map(({ jahre, items: tag }) => (
                    <ErinnerungKarte
                      key={jahre}
                      jahre={jahre}
                      titelbild={tag[0]}
                      anzahl={tag.length}
                      onOeffnen={() => setOffen({ id: tag[0].id, index: liste.indexOf(tag[0]) })}
                    />
                  ))}
                </div>
              </section>
            )}
            {gruppen.map((gruppe) => (
              <section
                key={gruppe.schluessel}
                aria-label={gruppe.titel}
                className="mb-4"
                style={{ contentVisibility: 'auto', containIntrinsicSize: `auto ${Math.ceil(gruppe.items.length / 6) * kante + 32}px` }}
              >
                <h3 className="mb-1.5 text-xs font-semibold text-on-surface">{gruppe.titel}</h3>
                <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${kante}px, 1fr))` }}>
                  {gruppe.items.map((item) => (
                    <Kachel
                      key={item.id}
                      item={item}
                      ausgewaehlt={auswahl ? auswahl.has(item.id) : undefined}
                      offline={Boolean(item.datei && offline.has(item.datei.original.id))}
                      tabHalt={item.id === tabHalt}
                      onKlick={kachelKlick}
                      onLangdruck={kachelLangdruck}
                      onMenue={kachelMenueOeffnen}
                      onFokus={setFokusId}
                    />
                  ))}
                </div>
              </section>
            ))}
          </SichtbarKontext.Provider>
        )}
      </div>

      {ziehen && <Ablageflaeche text={t('mss.vault.fotos.ablegenHochladen')} />}

      {auswahl && (
        <Auswahlleiste variante="fuss" anzahlLabel={auswahlLabel} aktionen={aktionen} abbrechenLabel={t('mss.vault.dateien.auswahlBeenden')} onAbbrechen={auswahlLeeren} />
      )}

      <Kontextmenue
        ort={kachelMenue && menueItem ? { x: kachelMenue.x, y: kachelMenue.y } : null}
        items={menueItem ? kachelEintraege(menueItem) : []}
        label={menueItem ? t('mss.vault.dateien.aktionenFuer', { name: menueItem.service }) : t('mss.vault.dateien.aktionen')}
        ausloeser={kachelMenue?.ausloeser}
        onSchliessen={() => setKachelMenue(null)}
      />

      <Kontextmenue
        ort={albumMenue ? { x: albumMenue.x, y: albumMenue.y } : null}
        items={albumMenue ? albumMenueEintraege(gewaehltIds) : []}
        label={t('mss.vault.fotos.zuAlbum')}
        ausloeser={albumMenue?.ausloeser}
        onSchliessen={() => setAlbumMenue(null)}
      />

      {offenItem && userKey && (
        <GalerieLichtbox
          item={offenItem}
          index={offenIndex}
          anzahl={liste.length}
          userKey={userKey}
          istOffline={Boolean(offenItem.datei && offline.has(offenItem.datei.original.id))}
          onOffline={offlineUmschalten}
          onSchliessen={() => setOffen(null)}
          onBlaettern={(richtung) => {
            const ziel = liste[offenIndex + richtung]
            if (ziel) setOffen({ id: ziel.id, index: offenIndex + richtung })
          }}
          onArchivieren={(id) => void archivieren([id])}
          onPapierkorb={(id) => void inPapierkorb([id])}
        />
      )}
    </div>
  )
}

interface Anzeige {
  id: string
  url: string | null
  /** Zeigt `url` schon das Original? */
  original: boolean
  anteil: number | null
  /** Warum nichts zu sehen ist: ohne Netz, oder das Laden scheiterte. */
  fehler: false | LadeFehler
}

function GalerieLichtbox({
  item,
  index,
  anzahl,
  userKey,
  istOffline,
  onOffline,
  onSchliessen,
  onBlaettern,
  onArchivieren,
  onPapierkorb,
}: {
  item: VaultItem
  index: number
  anzahl: number
  userKey: CryptoKey
  istOffline: boolean
  onOffline: (item: VaultItem) => Promise<void> | void
  onSchliessen: () => void
  onBlaettern: (richtung: 1 | -1) => void
  onArchivieren: (id: string) => void
  onPapierkorb: (id: string) => void
}) {
  const { t, i18n } = useTranslation()
  const upload = useTresorUploads((s) => s.je[item.id])
  const [anzeige, setAnzeige] = useState<Anzeige>({ id: item.id, url: null, original: false, anteil: null, fehler: false })
  const [spielt, setSpielt] = useState(false)
  const [bearbeiten, setBearbeiten] = useState(false)
  const laufend = useRef<AbortController | null>(null)
  const letzterVersuch = useRef<'vorschau' | 'original'>('vorschau')
  const datei = item.datei!
  const video = istVideo(item)
  // Ein Sync baut die Einträge neu auf; das offene Bild soll dabei nicht neu laden.
  const aktuell = useRef({ id: item.id, datei })
  aktuell.current = { id: item.id, datei }
  const ladenRef = useRef<(welcher: 'vorschau' | 'original') => Promise<void>>(async () => {})

  /** Lädt einen Blob dieses Eintrags und zeigt ihn, solange derselbe Eintrag offen ist. */
  const laden = useCallback(
    async (welcher: 'vorschau' | 'original') => {
      laufend.current?.abort()
      const abbruch = new AbortController()
      laufend.current = abbruch
      letzterVersuch.current = welcher
      const { id, datei } = aktuell.current
      const kopf = welcher === 'vorschau' ? datei.vorschau : datei.original
      const typ = welcher === 'vorschau' ? 'image/webp' : datei.typ
      if (welcher === 'original') setAnzeige((a) => ({ ...a, anteil: 0 }))
      try {
        const blob = await blobLesen(kopf, id, userKey, typ, {
          // Auch die Vorschau fällt nach ZULETZT_GRENZE heraus; tausend davon füllten sonst das Gerät.
          zuletzt: true,
          signal: abbruch.signal,
          fortschritt: welcher === 'original' ? (anteil) => setAnzeige((a) => (a.id === id ? { ...a, anteil } : a)) : undefined,
        })
        if (abbruch.signal.aborted || useVaultStore.getState().userKey !== userKey) return
        const url = ansichtOeffnen(blob)
        setAnzeige((a) => {
          if (a.id !== id) {
            ansichtSchliessen(url)
            return a
          }
          if (a.url) ansichtSchliessen(a.url)
          return { ...a, url, original: welcher === 'original', anteil: null, fehler: false }
        })
      } catch {
        if (abbruch.signal.aborted) return
        const fehler = ladeFehler()
        if (welcher === 'vorschau') {
          // Ein Video ohne Standbild lässt sich trotzdem abspielen.
          if (datei.typ.startsWith('video/')) return
          // Ohne Netz liegt vielleicht das Original auf dem Gerät (angeheftet oder zuletzt geöffnet), die Vorschau nicht.
          if (fehler === 'offline') return void ladenRef.current('original')
        }
        setAnzeige((a) => (a.id === id ? { ...a, fehler: a.url ? false : fehler, anteil: null } : a))
      }
    },
    [userKey],
  )
  ladenRef.current = laden

  const erneut = useCallback(() => {
    setAnzeige((a) => ({ ...a, fehler: false }))
    void laden(letzterVersuch.current)
  }, [laden])

  useNeuBeiNetz(anzeige.fehler, erneut)

  useEffect(() => {
    setAnzeige((a) => {
      if (a.url) ansichtSchliessen(a.url)
      return { id: item.id, url: null, original: false, anteil: null, fehler: false }
    })
    setSpielt(false)
    // Ohne Vorschaubild (etwa HEIC) gleich das Original; Videos erst auf Wunsch.
    if (aktuell.current.datei.vorschau.echt > 0) void laden('vorschau')
    else if (!aktuell.current.datei.typ.startsWith('video/')) void laden('original')
    return () => laufend.current?.abort()
    // Nach dem Bearbeiten hat die Datei ein neues Original und damit eine neue Vorschau.
  }, [item.id, datei.original.id, laden])

  // Beim Schließen die letzte Ansicht freigeben.
  const letzteUrl = useRef<string | null>(null)
  letzteUrl.current = anzeige.url
  useEffect(() => () => {
    if (letzteUrl.current) ansichtSchliessen(letzteUrl.current)
  }, [])

  const abspielen = () => {
    setSpielt(true)
    void laden('original')
  }

  const speichern = () => dateiAufsGeraet(item, userKey)

  const aufnahme = datei.aufgenommen
    ? formatZeitpunkt(datei.aufgenommen, i18n.language, { utc: true })
    : null
  const hinzugefuegt = formatZeitpunkt(item.createdAt, i18n.language)
  // Ohne EXIF steht in der Kopfzeile das Änderungsdatum der Datei.
  const datum =
    aufnahme ??
    (datei.geaendert ? formatZeitpunkt(datei.geaendert, i18n.language) : undefined)

  const knopf = 'text-white/85 hover:bg-white/10 hover:text-white'

  // Gedrehte und zugeschnittene Fotos sind neue Fassungen; die Infoleiste zeigt die früheren.
  const info = (
    <TresorDateiInfo
      item={item}
      angaben={[
        [t('mss.vault.fotos.info.name'), item.service],
        [t('mss.vault.fotos.info.aufgenommen'), aufnahme],
        [t('mss.vault.fotos.info.kamera'), datei.kamera],
        [t('mss.vault.fotos.info.masse'), datei.breite && datei.hoehe ? `${datei.breite} × ${datei.hoehe}` : null],
        [t('mss.vault.fotos.info.dauer'), datei.dauer ? formatDauer(datei.dauer) : null],
        [t('mss.vault.fotos.info.groesse'), formatBytes(datei.original.echt)],
        [t('mss.vault.fotos.info.hinzugefuegt'), hinzugefuegt],
      ]}
    />
  )

  let inhalt: React.ReactNode
  if (anzeige.fehler) {
    inhalt = (
      <LadeFehlerHinweis
        fehler={anzeige.fehler}
        offlineHinweis={t('mss.vault.fotos.offlineNichtDa', { aktion: t('mss.vault.dateien.offlineMachen') })}
        text={upload ? t('mss.vault.dateien.oeffnenFehler') : t('mss.vault.fotos.ladeFehler')}
        onErneut={erneut}
      />
    )
  } else if (video && anzeige.original && anzeige.url) {
    inhalt = <video src={anzeige.url} controls autoPlay className="max-h-full max-w-full" />
  } else {
    inhalt = (
      <div className="relative flex h-full w-full items-center justify-center">
        {anzeige.url && <img src={anzeige.url} alt={item.service} draggable={false} className="max-h-full max-w-full object-contain" />}
        {video && !spielt && (
          <Button type="button" variant="ghost" className={`absolute h-16 w-16 rounded-full bg-black/50 ${knopf}`} aria-label={t('mss.vault.fotos.abspielen')} onClick={abspielen}>
            <Play className="h-8 w-8 fill-current" />
          </Button>
        )}
        {anzeige.anteil !== null && (
          <div className="absolute bottom-4 w-64 rounded-lg bg-black/60 p-2">
            <ProgressBar value={anzeige.anteil * 100} label={t('mss.vault.dateien.wirdEntschluesselt')} />
          </div>
        )}
      </div>
    )
  }

  if (bearbeiten && anzeige.url) {
    return <TresorBildeditor item={item} vorschauUrl={anzeige.url} onFertig={() => setBearbeiten(false)} />
  }

  return (
    <Lichtbox
      kennung={item.id}
      titel={item.service}
      untertitel={datum}
      position={{ index, anzahl }}
      onSchliessen={onSchliessen}
      onVor={index < anzahl - 1 ? () => onBlaettern(1) : undefined}
      onZurueck={index > 0 ? () => onBlaettern(-1) : undefined}
      zoombar={!video}
      onZoom={() => {
        if (!anzeige.original) void laden('original')
      }}
      info={info}
      aktionen={
        <>
          {BEARBEITBAR.includes(datei.typ) && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={knopf}
              aria-label={t('mss.vault.bearbeiten.knopf')}
              disabled={!anzeige.url}
              onClick={() => setBearbeiten(true)}
            >
              <Crop className="h-4 w-4" />
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={knopf}
            aria-label={t(istOffline ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen')}
            onClick={() => void onOffline(item)}
          >
            <HardDriveDownload className={cx('h-4 w-4', istOffline && 'text-secondary')} />
          </Button>
          <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('mss.vault.dateien.speichern')} onClick={() => void speichern()}>
            <Download className="h-4 w-4" />
          </Button>
          <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('mss.vault.archivieren')} onClick={() => onArchivieren(item.id)}>
            <Archive className="h-4 w-4" />
          </Button>
          <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('mss.vault.inPapierkorb')} onClick={() => onPapierkorb(item.id)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </>
      }
    >
      {inhalt}
    </Lichtbox>
  )
}
