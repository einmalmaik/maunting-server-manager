/**
 * Der Bereich „Fotos“ im Tresor: alle Bilder und Videos als Raster, nach
 * Monaten gruppiert, mit Lichtbox.
 *
 * Das Raster zeigt nur Miniaturen (256 px, WebP), die Lichtbox die Vorschau
 * (1600 px) und erst beim Hineinzoomen oder Abspielen das Original. Alles
 * wird auf dem Gerät entschlüsselt; Monatsgruppen außerhalb des Bildschirms
 * überspringt der Browser (`content-visibility`), und Miniaturen werden erst
 * geladen, wenn ihre Kachel in die Nähe kommt.
 */

import React, { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ArrowLeft,
  Check,
  CheckSquare,
  Clock,
  Copy,
  Download,
  Film,
  FolderInput,
  FolderMinus,
  ImageIcon,
  Images,
  Library,
  Minus,
  Pencil,
  Play,
  Plus,
  Trash2,
  Upload,
} from 'lucide-react'
import { ActionMenu, Button, Dropdown, FileButton, Lichtbox, ProgressBar, type DropdownOption } from '@/Singra/UI'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useVaultStore, type VaultItem } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, blobLesen, useTresorUploads } from './tresorDateien'
import { miniaturenVorladen, useMiniatur } from './tresorMiniaturen'
import { speichernUnter } from './tresorAnzeige'
import { gruppieren, hashesBerechnen } from './tresorAehnlich'

type Filter = 'alle' | 'videos' | 'kuerzlich' | 'aehnlich' | 'alben'

/** Kantenlänge der Kacheln je Zoomstufe. */
const KACHEL = [84, 128, 200] as const

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

function dauerText(sekunden: number): string {
  const s = Math.round(sekunden)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const rest = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`
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

const Kachel = memo(function Kachel({
  item,
  ausgewaehlt,
  onKlick,
}: {
  item: VaultItem
  /** Nur im Auswahlmodus gesetzt. */
  ausgewaehlt?: boolean
  onKlick: (id: string) => void
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLButtonElement>(null)
  const sichtbar = useSichtbar(ref)
  const url = useMiniatur(item.datei?.miniatur, item.id, sichtbar)
  const upload = useTresorUploads((s) => s.je[item.id])
  const video = istVideo(item)
  const Symbol = video ? Film : ImageIcon
  return (
    <button
      ref={ref}
      type="button"
      onClick={() => onKlick(item.id)}
      aria-label={item.service}
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
      {video && (
        <span className="absolute bottom-1 right-1 inline-flex items-center gap-0.5 rounded bg-black/60 px-1 text-label-sm font-medium text-white">
          <Play className="h-2.5 w-2.5 fill-current" />
          {item.datei?.dauer ? dauerText(item.datei.dauer) : null}
        </span>
      )}
      {upload && (
        <span className="absolute inset-x-1 bottom-1">
          {upload.fehler === 'speicherVoll' ? (
            <span className="rounded bg-black/70 px-1 text-label-sm text-status-destructive">{t('mss.vault.dateien.speicherVoll')}</span>
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

export function TresorGalerie({ suche = '' }: { suche?: string }) {
  const { t, i18n } = useTranslation()
  const items = useVaultStore((s) => s.items)
  const userKey = useVaultStore((s) => s.userKey)
  const dateiHinzufuegen = useVaultStore((s) => s.dateiHinzufuegen)
  const albumAnlegen = useVaultStore((s) => s.albumAnlegen)
  const albumAendern = useVaultStore((s) => s.albumAendern)
  const saveItem = useVaultStore((s) => s.saveItem)
  const trashItem = useVaultStore((s) => s.trashItem)
  const setArchived = useVaultStore((s) => s.setArchived)
  const [filter, setFilterRoh] = useState<Filter>('alle')
  const [quelle, setQuelle] = useState('')
  const [aehnlich, setAehnlich] = useState<{ fertig: number; gesamt: number; gruppen: string[][] | null }>({ fertig: 0, gesamt: 0, gruppen: null })
  const [albumId, setAlbumId] = useState<string | null>(null)
  const [auswahl, setAuswahl] = useState<Set<string> | null>(null)
  const [stufe, setStufe] = useState(1)
  const [offen, setOffen] = useState<{ id: string; index: number } | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)
  const [ziehen, setZiehen] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const beobachten = useBeobachter(scroller)

  const setFilter = (neu: Filter) => {
    setFilterRoh(neu)
    setAlbumId(null)
    setAuswahl(null)
  }

  const medien = useMemo(() => items.filter((i) => istMedium(i) && !i.trashedAt && !i.archivedAt), [items])
  const alben = useMemo(
    () => items.filter((i) => i.category === 'album' && !i.trashedAt && !i.archivedAt).sort((a, b) => a.service.localeCompare(b.service)),
    [items],
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
    const alle = useVaultStore.getState().items.filter((i) => istMedium(i) && !i.trashedAt && !i.archivedAt)
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

  const auswahlAn = auswahl !== null
  const kachelKlick = useCallback(
    (id: string) => {
      if (!auswahlAn) {
        setOffen({ id, index: 0 })
        return
      }
      setAuswahl((alt) => {
        const neu = new Set(alt)
        if (neu.has(id)) neu.delete(id)
        else neu.add(id)
        return neu
      })
    },
    [auswahlAn],
  )

  const fehlerZeigen = (err: unknown) => toast.error(err instanceof Error ? err.message : String(err))

  const hochladen = async (dateien: File[]) => {
    const passend = dateien.filter((d) => d.type.startsWith('image/') || d.type.startsWith('video/'))
    setVorbereitung((n) => n + passend.length)
    const neue: string[] = []
    for (const datei of passend) {
      try {
        neue.push(await dateiHinzufuegen(datei))
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t('mss.vault.dateien.hochladenFehler'))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
    // Im offenen Album hochgeladen: gehört auch hinein.
    if (album && neue.length > 0) await albumAendern(album.id, { hinzu: neue }).catch(fehlerZeigen)
  }

  const neuesAlbum = async (eintraege: string[]) => {
    const name = await prompt({ message: t('mss.vault.fotos.albumName'), confirmText: t('mss.vault.fotos.neuesAlbum') })
    if (!name?.trim()) return
    try {
      const id = await albumAnlegen(name.trim(), eintraege)
      setAuswahl(null)
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
      setAuswahl(null)
      toast.success(t('mss.vault.fotos.hinzugefuegt', { name: a.service }))
    } catch (err) {
      fehlerZeigen(err)
    }
  }

  const albumUmbenennen = async (a: VaultItem) => {
    const name = await prompt({ message: t('mss.vault.dateien.neuerName'), defaultValue: a.service })
    if (!name?.trim() || name.trim() === a.service) return
    await saveItem({ ...a, service: name.trim() }).catch(fehlerZeigen)
  }

  const fuerAuswahl = async (arbeit: (id: string) => Promise<void>, meldung?: string) => {
    if (!auswahl) return
    try {
      for (const id of auswahl) await arbeit(id)
      if (meldung) toast.success(meldung)
      setAuswahl(null)
    } catch (err) {
      fehlerZeigen(err)
    }
  }

  const filterTabs: TabDef<Filter>[] = [
    { id: 'alle', labelKey: 'mss.vault.fotos.alle', icon: Images },
    { id: 'videos', labelKey: 'mss.vault.fotos.videos', icon: Film },
    { id: 'kuerzlich', labelKey: 'mss.vault.fotos.kuerzlich', icon: Clock },
    { id: 'aehnlich', labelKey: 'mss.vault.fotos.aehnlich', icon: Copy },
    { id: 'alben', labelKey: 'mss.vault.fotos.alben', icon: Library },
  ]

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

  const kante = KACHEL[stufe]
  const albenUebersicht = filter === 'alben' && !album
  const anzahlAuswahl = auswahl?.size ?? 0

  let leiste: React.ReactNode
  if (auswahl) {
    const ids = [...auswahl]
    leiste = (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-semibold text-on-surface">{t('mss.vault.fotos.ausgewaehlt', { count: anzahlAuswahl })}</span>
        <ActionMenu
          label={t('mss.vault.fotos.zuAlbum')}
          icon={<FolderInput className="h-3.5 w-3.5" />}
          disabled={anzahlAuswahl === 0}
          items={[
            ...alben
              .filter((a) => a.id !== album?.id)
              .map((a) => ({ key: a.id, label: a.service, icon: <Images className="h-3.5 w-3.5" />, onSelect: () => void zuAlbum(a, ids) })),
            {
              key: 'neu',
              label: t('mss.vault.fotos.neuesAlbum'),
              icon: <Plus className="h-3.5 w-3.5" />,
              separatorBefore: alben.length > 0,
              onSelect: () => void neuesAlbum(ids),
            },
          ]}
        />
        {album && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={anzahlAuswahl === 0}
            onClick={() =>
              void albumAendern(album.id, { weg: ids })
                .then(() => setAuswahl(null))
                .catch(fehlerZeigen)
            }
          >
            <FolderMinus className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.fotos.ausAlbum')}
          </Button>
        )}
        <Button type="button" variant="secondary" size="sm" disabled={anzahlAuswahl === 0} onClick={() => void fuerAuswahl((id) => setArchived(id, true))}>
          <Archive className="mr-1 h-3.5 w-3.5" />
          {t('mss.vault.archivieren')}
        </Button>
        <Button
          type="button"
          variant="destructive"
          size="sm"
          disabled={anzahlAuswahl === 0}
          onClick={() => void fuerAuswahl((id) => trashItem(id), t('mss.vault.inPapierkorbGelegt'))}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          {t('mss.vault.inPapierkorb')}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setAuswahl(null)}>
          {t('common.cancel')}
        </Button>
      </div>
    )
  } else {
    leiste = (
      <div className="flex items-center gap-1">
        {!albenUebersicht && (
          <>
            <Button type="button" variant="ghost" size="icon" aria-label={t('mss.vault.fotos.kleiner')} disabled={stufe === 0} onClick={() => setStufe((s) => Math.max(0, s - 1))}>
              <Minus className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t('mss.vault.fotos.groesser')}
              disabled={stufe === KACHEL.length - 1}
              onClick={() => setStufe((s) => Math.min(KACHEL.length - 1, s + 1))}
            >
              <Plus className="h-4 w-4" />
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={liste.length === 0} onClick={() => setAuswahl(new Set())}>
              <CheckSquare className="mr-1 h-3.5 w-3.5" />
              {t('mss.vault.fotos.auswaehlen')}
            </Button>
          </>
        )}
        {albenUebersicht ? (
          <Button type="button" variant="primary" size="sm" onClick={() => void neuesAlbum([])}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.fotos.neuesAlbum')}
          </Button>
        ) : (
          <FileButton multiple accept="image/*,video/*" size="sm" variant="primary" onFiles={(dateien) => void hochladen(dateien)}>
            <Upload className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.dateien.hochladen')}
          </FileButton>
        )}
      </div>
    )
  }

  return (
    <div
      ref={scroller}
      className={`flex-1 overflow-y-auto px-4 py-3 ${ziehen ? 'bg-primary/5 outline-dashed outline-2 outline-primary/40 -outline-offset-4' : ''}`}
      onDragOver={(e) => {
        if (albenUebersicht || !e.dataTransfer.types.includes('Files')) return
        e.preventDefault()
        setZiehen(true)
      }}
      onDragLeave={() => setZiehen(false)}
      onDrop={(e) => {
        e.preventDefault()
        setZiehen(false)
        const dateien = Array.from(e.dataTransfer.files)
        if (dateien.length > 0) void hochladen(dateien)
      }}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <TabBar tabs={filterTabs} active={filter} onChange={setFilter} embedded ariaLabel={t('mss.vault.fotos.filter')} />
          {!albenUebersicht && quellen.length > 2 && (
            <Dropdown
              value={quelle}
              onChange={setQuelle}
              options={quellen}
              aria-label={t('mss.vault.fotos.quelle.titel')}
              className="w-48"
            />
          )}
        </div>
        {leiste}
      </div>

      {album && (
        <div className="mb-3 flex items-center gap-2">
          <Button type="button" variant="ghost" size="icon" aria-label={t('mss.vault.fotos.alleAlben')} onClick={() => setAlbumId(null)}>
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
                onSelect: () => void trashItem(album.id).then(() => toast.success(t('mss.vault.inPapierkorbGelegt'))),
              },
            ]}
          />
        </div>
      )}

      {vorbereitung > 0 && (
        <p className="mb-2 text-label-sm text-on-surface-variant">{t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}</p>
      )}

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
          <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-on-surface-variant">
            <Library className="mb-2 h-6 w-6 opacity-60" />
            {t('mss.vault.fotos.keineAlben')}
          </div>
        ) : (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))' }}>
            {alben.map((a) => {
              const inhalt = albumInhalt(a)
              return <AlbumKarte key={a.id} album={a} titelbild={inhalt[0]} anzahl={inhalt.length} onOeffnen={() => setAlbumId(a.id)} />
            })}
          </div>
        )
      ) : liste.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-on-surface-variant">
          <Images className="mb-2 h-6 w-6 opacity-60" />
          {t(
            album
              ? 'mss.vault.fotos.albumLeer'
              : filter === 'aehnlich'
                ? 'mss.vault.fotos.keineAehnlichen'
                : filter === 'alle' && !suche.trim()
                  ? 'mss.vault.fotos.leer'
                  : 'mss.vault.fotos.leerFilter',
          )}
        </div>
      ) : (
        <SichtbarKontext.Provider value={beobachten}>
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
                  <Kachel key={item.id} item={item} ausgewaehlt={auswahl ? auswahl.has(item.id) : undefined} onKlick={kachelKlick} />
                ))}
              </div>
            </section>
          ))}
        </SichtbarKontext.Provider>
      )}

      {offenItem && userKey && (
        <GalerieLichtbox
          item={offenItem}
          index={offenIndex}
          anzahl={liste.length}
          userKey={userKey}
          onSchliessen={() => setOffen(null)}
          onBlaettern={(richtung) => {
            const ziel = liste[offenIndex + richtung]
            if (ziel) setOffen({ id: ziel.id, index: offenIndex + richtung })
          }}
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
  fehler: boolean
}

function GalerieLichtbox({
  item,
  index,
  anzahl,
  userKey,
  onSchliessen,
  onBlaettern,
}: {
  item: VaultItem
  index: number
  anzahl: number
  userKey: CryptoKey
  onSchliessen: () => void
  onBlaettern: (richtung: 1 | -1) => void
}) {
  const { t, i18n } = useTranslation()
  const trashItem = useVaultStore((s) => s.trashItem)
  const setArchived = useVaultStore((s) => s.setArchived)
  const [anzeige, setAnzeige] = useState<Anzeige>({ id: item.id, url: null, original: false, anteil: null, fehler: false })
  const [spielt, setSpielt] = useState(false)
  const laufend = useRef<AbortController | null>(null)
  const datei = item.datei!
  const video = istVideo(item)
  // Ein Sync baut die Einträge neu auf; das offene Bild soll dabei nicht neu laden.
  const aktuell = useRef({ id: item.id, datei })
  aktuell.current = { id: item.id, datei }

  /** Lädt einen Blob dieses Eintrags und zeigt ihn, solange derselbe Eintrag offen ist. */
  const laden = useCallback(
    async (welcher: 'vorschau' | 'original') => {
      laufend.current?.abort()
      const abbruch = new AbortController()
      laufend.current = abbruch
      const { id, datei } = aktuell.current
      const kopf = welcher === 'vorschau' ? datei.vorschau : datei.original
      const typ = welcher === 'vorschau' ? 'image/webp' : datei.typ
      if (welcher === 'original') setAnzeige((a) => ({ ...a, anteil: 0 }))
      try {
        const blob = await blobLesen(kopf, id, userKey, typ, {
          cachen: welcher === 'vorschau',
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
          return { ...a, url, original: welcher === 'original', anteil: null }
        })
      } catch {
        if (!abbruch.signal.aborted) setAnzeige((a) => (a.id === id ? { ...a, fehler: !a.url, anteil: null } : a))
      }
    },
    [userKey],
  )

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
  }, [item.id, laden])

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

  const speichern = async () => {
    try {
      const blob = await blobLesen(datei.original, item.id, userKey, datei.typ)
      if (useVaultStore.getState().userKey !== userKey) return
      const url = ansichtOeffnen(blob)
      speichernUnter(url, item.service)
      setTimeout(() => ansichtSchliessen(url), 60_000)
    } catch {
      toast.error(t('mss.vault.dateien.oeffnenFehler'))
    }
  }

  const aufnahme = datei.aufgenommen
    ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(datei.aufgenommen)
    : null
  const hinzugefuegt = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(item.createdAt)
  // Ohne EXIF steht in der Kopfzeile das Änderungsdatum der Datei.
  const datum =
    aufnahme ??
    (datei.geaendert ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(datei.geaendert) : undefined)

  const knopf = 'text-white/85 hover:bg-white/10 hover:text-white'

  const info = (
    <dl className="space-y-3">
      {[
        [t('mss.vault.fotos.info.name'), item.service],
        [t('mss.vault.fotos.info.aufgenommen'), aufnahme],
        [t('mss.vault.fotos.info.kamera'), datei.kamera],
        [t('mss.vault.fotos.info.masse'), datei.breite && datei.hoehe ? `${datei.breite} × ${datei.hoehe}` : null],
        [t('mss.vault.fotos.info.dauer'), datei.dauer ? dauerText(datei.dauer) : null],
        [t('mss.vault.fotos.info.groesse'), formatBytes(datei.original.echt)],
        [t('mss.vault.fotos.info.hinzugefuegt'), hinzugefuegt],
      ]
        .filter(([, wert]) => wert)
        .map(([name, wert]) => (
          <div key={name}>
            <dt className="text-label-sm text-white/55">{name}</dt>
            <dd className="break-words text-white/90">{wert}</dd>
          </div>
        ))}
    </dl>
  )

  let inhalt: React.ReactNode
  if (anzeige.fehler) {
    inhalt = <p className="text-sm text-white/70">{t('mss.vault.dateien.oeffnenFehler')}</p>
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
          <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('mss.vault.dateien.speichern')} onClick={() => void speichern()}>
            <Download className="h-4 w-4" />
          </Button>
          <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('mss.vault.archivieren')} onClick={() => void setArchived(item.id, true)}>
            <Archive className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={knopf}
            aria-label={t('mss.vault.inPapierkorb')}
            onClick={() => void trashItem(item.id).then(() => toast.success(t('mss.vault.inPapierkorbGelegt')))}
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
