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
import { Archive, Clock, Download, Film, ImageIcon, Images, Minus, Monitor, Play, Plus, Trash2, Upload } from 'lucide-react'
import { Button, FileButton, Lichtbox, ProgressBar } from '@/Singra/UI'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { toast } from '@/stores/toastStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useVaultStore, type VaultItem } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, blobLesen, useTresorUploads } from './tresorDateien'
import { miniaturenVorladen, useMiniatur } from './tresorMiniaturen'
import { speichernUnter } from './tresorAnzeige'

type Filter = 'alle' | 'videos' | 'screenshots' | 'kuerzlich'

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

export function istScreenshot(item: VaultItem): boolean {
  return !istVideo(item) && SCREENSHOT.test(item.service)
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

const Kachel = memo(function Kachel({ item, onOeffnen }: { item: VaultItem; onOeffnen: (id: string) => void }) {
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
      onClick={() => onOeffnen(item.id)}
      aria-label={item.service}
      className="group relative aspect-square overflow-hidden rounded-md bg-surface-container focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
    >
      {url ? (
        <img src={url} alt="" draggable={false} className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]" />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-on-surface-variant/60">
          <Symbol className="h-6 w-6" />
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

export function TresorGalerie({ suche = '' }: { suche?: string }) {
  const { t, i18n } = useTranslation()
  const items = useVaultStore((s) => s.items)
  const userKey = useVaultStore((s) => s.userKey)
  const dateiHinzufuegen = useVaultStore((s) => s.dateiHinzufuegen)
  const [filter, setFilter] = useState<Filter>('alle')
  const [stufe, setStufe] = useState(1)
  const [offen, setOffen] = useState<{ id: string; index: number } | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)
  const [ziehen, setZiehen] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const beobachten = useBeobachter(scroller)

  const medien = useMemo(() => items.filter((i) => istMedium(i) && !i.trashedAt && !i.archivedAt), [items])

  const liste = useMemo(() => {
    const wort = suche.trim().toLocaleLowerCase()
    const auswahl = medien.filter(
      (i) =>
        (filter === 'videos' ? istVideo(i) : filter === 'screenshots' ? istScreenshot(i) : true) &&
        (!wort || i.service.toLocaleLowerCase().includes(wort) || !!i.datei?.kamera?.toLocaleLowerCase().includes(wort)),
    )
    return auswahl.sort((a, b) => zeitpunkt(b, filter).ms - zeitpunkt(a, filter).ms)
  }, [medien, filter, suche])

  const gruppen = useMemo(() => {
    const ergebnis: { schluessel: string; titel: string; items: VaultItem[] }[] = []
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
  }, [liste, filter, i18n.language])

  // Offline sollen alle Miniaturen da sein: im Hintergrund nachladen, was fehlt.
  const anzahlMedien = medien.length
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

  const oeffnen = useCallback((id: string) => {
    setOffen({ id, index: 0 })
  }, [])

  const hochladen = async (dateien: File[]) => {
    const passend = dateien.filter((d) => d.type.startsWith('image/') || d.type.startsWith('video/'))
    setVorbereitung((n) => n + passend.length)
    for (const datei of passend) {
      try {
        await dateiHinzufuegen(datei)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t('mss.vault.dateien.hochladenFehler'))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
  }

  const filterTabs: TabDef<Filter>[] = [
    { id: 'alle', labelKey: 'mss.vault.fotos.alle', icon: Images },
    { id: 'videos', labelKey: 'mss.vault.fotos.videos', icon: Film },
    { id: 'screenshots', labelKey: 'mss.vault.fotos.screenshots', icon: Monitor },
    { id: 'kuerzlich', labelKey: 'mss.vault.fotos.kuerzlich', icon: Clock },
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

  return (
    <div
      ref={scroller}
      className={`flex-1 overflow-y-auto px-4 py-3 ${ziehen ? 'bg-primary/5 outline-dashed outline-2 outline-primary/40 -outline-offset-4' : ''}`}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return
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
        <TabBar tabs={filterTabs} active={filter} onChange={setFilter} embedded ariaLabel={t('mss.vault.fotos.filter')} />
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
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
            aria-label={t('mss.vault.fotos.groesser')}
            disabled={stufe === KACHEL.length - 1}
            onClick={() => setStufe((s) => Math.min(KACHEL.length - 1, s + 1))}
          >
            <Plus className="h-4 w-4" />
          </Button>
          <FileButton multiple accept="image/*,video/*" size="sm" variant="primary" onFiles={(dateien) => void hochladen(dateien)}>
            <Upload className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.dateien.hochladen')}
          </FileButton>
        </div>
      </div>

      {vorbereitung > 0 && (
        <p className="mb-2 text-label-sm text-on-surface-variant">{t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}</p>
      )}

      {liste.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-on-surface-variant">
          <Images className="mb-2 h-6 w-6 opacity-60" />
          {t(filter === 'alle' && !suche.trim() ? 'mss.vault.fotos.leer' : 'mss.vault.fotos.leerFilter')}
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
                  <Kachel key={item.id} item={item} onOeffnen={oeffnen} />
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
