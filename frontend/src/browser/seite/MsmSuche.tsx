/**
 * Die Maunting-Search-Fläche (`msb://suche?q=…`).
 *
 * Die Treffer kommen vom eigenen Server (`POST /api/browser/suche`), der über
 * seine Metasuch-Instanz sucht. Keine Drittanbieter-Abhängigkeiten: Favicons
 * werden über die Rust-Brücke (`SeitenSymbol`) geholt, Vorschaubilder und
 * Wikipedia-Wissensdaten über den eigenen Server-Proxy.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  Globe,
  Image as ImageIcon,
  Loader2,
  Newspaper,
  Play,
  Search,
  Video,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api, isNetworkOrOfflineError } from '@/api/client'
import { Badge, Button, Dropdown, TabBar, type DropdownOption, type TabDef } from '@/Singra/UI'
import { useZurueckSchliesst } from '@/Singra/UI/useZurueckSchliesst'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { SuchEingabeLeiste } from '../kopf/SuchEingabeLeiste'
import { NACHRICHTEN_THEMEN, useEinstellungenStore } from '../services/einstellungenStore'
import { msmSucheAdresse, msmSucheBegriff } from '../services/intern'
import { nativ, type SchutzStand } from '../services/nativ'
import { suchAdresse } from '../services/searchEngines'
import { useTabsStore, type Tab } from '../services/tabsStore'
import { SeitenSymbol } from './SeitenSymbol'
import { SuchBild } from './SuchBild'
import { auswahl, useNachrichten } from './start/nachrichtenDaten'
import { SuchzusammenfassungKachel } from './suchzusammenfassung/SuchzusammenfassungKachel'

export interface Treffer {
  titel: string
  url: string
  inhalt: string
  bild?: string
  published?: string
}

export interface WissensAttribut {
  label: string
  value: string
}

export interface WissensUrl {
  title: string
  url: string
}

export interface WissensPanelDaten {
  titel: string
  untertitel?: string
  inhalt: string
  bild?: string
  attribute?: WissensAttribut[]
  urls?: WissensUrl[]
}

export type SuchKategorie = 'web' | 'images' | 'videos' | 'news'

type Stand =
  | { art: 'laedt' }
  | {
      art: 'da'
      treffer: Treffer[]
      vorschlaege: string[]
      infobox?: WissensPanelDaten | null
      seite: number
      mehr: boolean
      weitereLaedt: boolean
      dauerSek: number
    }
  | { art: 'fehler'; zuViele: boolean }
  | { art: 'offline' }
  | { art: 'nichtEingerichtet' }

/** Zuletzt gesehene Treffer, damit Zurück nicht noch einmal sucht. Nur im Speicher. */
const GEMERKT_MAX = 15
const gemerkt = new Map<string, Extract<Stand, { art: 'da' }>>()

function merken(schluessel: string, stand: Extract<Stand, { art: 'da' }>) {
  gemerkt.delete(schluessel)
  gemerkt.set(schluessel, { ...stand, weitereLaedt: false })
  if (gemerkt.size > GEMERKT_MAX) gemerkt.delete(gemerkt.keys().next().value as string)
}

/** Für Tests. */
export function gemerktLeeren() {
  gemerkt.clear()
}

/** Sichere Suche, sobald Erwachsenen-Inhalte gesperrt sind; im Zweifel ja. */
export function sichereSuche(stand: SchutzStand | null): boolean {
  if (!stand || stand.beschaedigt) return true
  return stand.regeln.aktiv && stand.regeln.kategorien.includes('erwachsene')
}

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function pfad(url: string): string {
  try {
    const u = new URL(url)
    const teile = u.pathname.split('/').filter(Boolean)
    if (teile.length === 0) return ''
    return ' › ' + teile.slice(0, 3).join(' › ')
  } catch {
    return ''
  }
}

/**
 * Erzeugt echte, kontextbezogene Vertiefungen aus den Suchvorschlägen und den
 * Titeln der Treffer — immer, wenn es Material dazu gibt. Keine Sperrliste: was
 * eine sinnvolle Frage ist, entscheiden die Vorschläge der Suche und die
 * Treffer, nicht eine Wortliste im Code, die ohnehin nie alle Einzelwörter kennt.
 */
export function kontextFragen(
  begriff: string,
  vorschlaege: string[],
  treffer: Treffer[],
): { frage: string; antwort?: string; url?: string }[] {
  const fragen: { frage: string; antwort?: string; url?: string }[] = []
  const gesehen = new Set<string>()
  const bKlein = begriff.trim().toLowerCase()

  // 1. Echte Fragen oder vertiefende Aspekte aus den Suchvorschlägen
  for (const v of vorschlaege) {
    const text = v.trim()
    if (!text || text.toLowerCase() === bKlein || gesehen.has(text.toLowerCase())) continue
    gesehen.add(text.toLowerCase())
    const passenderTreffer = treffer.find(
      (tr) =>
        tr.inhalt &&
        text
          .toLowerCase()
          .split(/\s+/)
          .filter((w) => w.length > 3)
          .some((w) => tr.inhalt.toLowerCase().includes(w) || tr.titel.toLowerCase().includes(w)),
    )
    fragen.push({
      frage: text.endsWith('?') ? text : `${text.charAt(0).toUpperCase()}${text.slice(1)}`,
      antwort: passenderTreffer?.inhalt || treffer[0]?.inhalt,
      url: passenderTreffer?.url || treffer[0]?.url,
    })
    if (fragen.length >= 3) return fragen
  }

  // 2. Falls keine Suggestions vorlagen, kontextbezogene Themen aus den Top-Treffer-Titeln ableiten
  for (const tr of treffer.slice(0, 5)) {
    const titel = tr.titel.trim()
    const auszug = tr.inhalt.trim()
    if (!titel || !auszug || auszug.length < 40) continue
    const teile = titel.split(/\s+[–—|-]\s+|\s+:\s+/)
    const thema = teile[0]?.trim() || titel
    if (!thema || thema.toLowerCase() === bKlein || gesehen.has(thema.toLowerCase())) continue
    gesehen.add(thema.toLowerCase())
    fragen.push({
      frage: thema,
      antwort: auszug,
      url: tr.url,
    })
    if (fragen.length >= 3) break
  }

  return fragen
}

async function holen(
  begriff: string,
  seite: number,
  sprache: string,
  kategorie: SuchKategorie,
): Promise<{ treffer: Treffer[]; vorschlaege: string[]; infobox?: WissensPanelDaten | null; dauerSek: number }> {
  const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const sicher = sichereSuche(await nativ.schutzStand().catch(() => null))
  const katParam = kategorie === 'web' ? undefined : kategorie
  const antwort = await api<{
    treffer: Treffer[]
    vorschlaege?: string[]
    infobox?: WissensPanelDaten | null
  }>('/browser/suche', {
    method: 'POST',
    body: JSON.stringify({
      q: begriff,
      seite,
      sicher,
      sprache,
      ...(katParam ? { kategorie: katParam } : {}),
    }),
  })
  const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const dauerSek = Math.max(0.05, Math.round(((t1 - t0) / 1000) * 100) / 100)
  return {
    treffer: antwort.treffer,
    vorschlaege: antwort.vorschlaege ?? [],
    infobox: antwort.infobox ?? null,
    dauerSek,
  }
}

export function MsmSuche({ tab }: { tab: Tab }) {
  const { t, i18n } = useTranslation()
  const oeffnen = useTabsStore((s) => s.oeffnen)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const startseite = useTabsStore((s) => s.startseite)
  const begriff = msmSucheBegriff(tab.url) ?? ''
  // Ohne eigene Wahl gilt die Sprache des Browsers; der gespeicherte Wert setzt
  // sich nicht wieder zurück, wenn der Nutzer unterwegs umgestellt hat.
  const gespeicherteRegion = useEinstellungenStore((s) => s.suchRegion)
  const regionSetzen = useEinstellungenStore((s) => s.setzen)
  const standardSprache = gespeicherteRegion ?? (i18n.language.slice(0, 2) === 'en' ? 'en' : 'de')

  const [region, setRegion] = useState<string>(standardSprache)
  const [kategorie, setKategorie] = useState<SuchKategorie>('web')
  const [wissenFallback, setWissenFallback] = useState<WissensPanelDaten | null>(null)
  const [wissenMobilOffen, setWissenMobilOffen] = useState(false)

  const speicherSchluessel = `${tab.url}::${region}::${kategorie}`
  const [stand, setStand] = useState<Stand>(() => gemerkt.get(speicherSchluessel) ?? { art: 'laedt' })
  const [versuch, setVersuch] = useState(0)

  // Aktuelle News aus dem lokalen Nachrichten-Spiegel/Cache als Ergänzung
  const { stand: nachrichtenStand } = useNachrichten()
  const lokaleNews = nachrichtenStand ? auswahl(nachrichtenStand, NACHRICHTEN_THEMEN, begriff, 6) : []

  useEffect(() => {
    const schon = gemerkt.get(speicherSchluessel)
    if (schon && versuch === 0) {
      setStand(schon)
      return
    }
    if (!begriff) return
    let aktiv = true
    setStand({ art: 'laedt' })
    holen(begriff, 1, region, kategorie).then(
      ({ treffer, vorschlaege, infobox, dauerSek }) => {
        if (!aktiv) return
        const neu: Extract<Stand, { art: 'da' }> = {
          art: 'da',
          treffer,
          vorschlaege,
          infobox,
          seite: 1,
          mehr: treffer.length > 0,
          weitereLaedt: false,
          dauerSek,
        }
        merken(speicherSchluessel, neu)
        setStand(neu)
      },
      (fehler: unknown) => {
        if (!aktiv) return
        const code = (fehler as { code?: string | null })?.code
        if (code === 'BROWSER_SUCHE_NICHT_EINGERICHTET') setStand({ art: 'nichtEingerichtet' })
        else if (!code?.startsWith('BROWSER_SUCHE_') && isNetworkOrOfflineError(fehler)) setStand({ art: 'offline' })
        else setStand({ art: 'fehler', zuViele: code === 'BROWSER_SUCHE_ZU_VIELE' })
      },
    )
    return () => {
      aktiv = false
    }
  }, [speicherSchluessel, begriff, region, kategorie, versuch])

  // Dynamisches Wikipedia-Knowledge-Panel: die Suche liefert (bei manchen
  // Instanzen) eine Infobox, sonst liest das Panel die Wikipedia-API.
  // Gelesen wird nur auf der Web-Karte und nur mit einem echten Trefferstand.
  const wissenNötig =
    kategorie === 'web' && begriff.length > 1 && stand.art === 'da' && !stand.infobox

  useEffect(() => {
    if (!wissenNötig) return
    let aktiv = true
    api<{ wissen?: WissensPanelDaten | null }>('/browser/suche/wissen', {
      method: 'POST',
      body: JSON.stringify({ q: begriff, sprache: region.slice(0, 2) }),
    })
      .then((res) => {
        if (aktiv && res?.wissen) {
          setWissenFallback(res.wissen)
        }
      })
      .catch(() => {
        // Stilles Ausblenden, falls kein Wikipedia-Eintrag existiert
      })
    return () => {
      aktiv = false
    }
  }, [wissenNötig, begriff, region])

  // Ohne Netz lädt die Seite von selbst, sobald es wieder da ist.
  useEffect(() => {
    if (stand.art !== 'offline') return
    const wieder = () => setVersuch((v) => v + 1)
    window.addEventListener('online', wieder)
    return () => window.removeEventListener('online', wieder)
  }, [stand.art])

  const weitere = () => {
    if (stand.art !== 'da' || stand.weitereLaedt) return
    const bisher = stand
    setStand({ ...bisher, weitereLaedt: true })
    holen(begriff, bisher.seite + 1, region, kategorie).then(
      ({ treffer: neu, vorschlaege: neueVorschlaege }) => {
        const bekannt = new Set(bisher.treffer.map((x) => x.url))
        const dazu = neu.filter((x) => !bekannt.has(x.url))
        const kombiniertVorschlaege = Array.from(new Set([...bisher.vorschlaege, ...neueVorschlaege]))
        const naechster: Extract<Stand, { art: 'da' }> = {
          ...bisher,
          treffer: [...bisher.treffer, ...dazu],
          vorschlaege: kombiniertVorschlaege,
          seite: bisher.seite + 1,
          mehr: dazu.length > 0,
          weitereLaedt: false,
        }
        merken(speicherSchluessel, naechster)
        setStand(naechster)
      },
      () => setStand({ ...bisher, weitereLaedt: false, mehr: true }),
    )
  }

  const seiteWechseln = (zielSeite: number) => {
    if (stand.art !== 'da' || stand.weitereLaedt || zielSeite < 1 || zielSeite === stand.seite) return
    if (zielSeite === stand.seite + 1) {
      weitere()
      return
    }
    const bisher = stand
    setStand({ ...bisher, weitereLaedt: true })
    holen(begriff, zielSeite, region, kategorie).then(
      ({ treffer: neu, vorschlaege: neueVorschlaege, infobox, dauerSek }) => {
        const naechster: Extract<Stand, { art: 'da' }> = {
          ...bisher,
          treffer: neu,
          vorschlaege: neueVorschlaege.length > 0 ? neueVorschlaege : bisher.vorschlaege,
          infobox: infobox ?? bisher.infobox,
          seite: zielSeite,
          mehr: neu.length > 0,
          weitereLaedt: false,
          dauerSek,
        }
        merken(speicherSchluessel, naechster)
        setStand(naechster)
      },
      () => setStand({ ...bisher, weitereLaedt: false }),
    )
  }

  const onNeueTreffer = (neue: Treffer[]) => {
    setStand((prev) => {
      if (prev.art !== 'da') return prev
      const bekannte = new Set(prev.treffer.map((x) => x.url))
      const dazu = neue.filter((x) => !bekannte.has(x.url))
      if (dazu.length === 0) return prev
      const aktualisiert = { ...prev, treffer: [...prev.treffer, ...dazu] }
      merken(speicherSchluessel, aktualisiert)
      return aktualisiert
    })
  }

  const mitDuckDuckGo = () => {
    const ziel = suchAdresse(begriff, 'duckduckgo')
    if (ziel) oeffnen(ziel, tab.id)
  }

  const zielOeffnen = (url: string, imNeuenTab = false) => {
    if (imNeuenTab) {
      neuerTab(url, { nach: tab.id, privat: tab.privat, hintergrund: true })
    } else {
      oeffnen(url, tab.id)
    }
  }

  // Die gewählte Region bleibt über Suchen hinweg erhalten.
  const regionWaehlen = (wert: string) => {
    setRegion(wert)
    regionSetzen({ suchRegion: wert })
  }

  const kategorieTabs: TabDef<SuchKategorie>[] = useMemo(
    () => [
      { id: 'web', labelKey: 'browser.msmSuche.tabWeb', icon: Globe },
      { id: 'images', labelKey: 'browser.msmSuche.tabBilder', icon: ImageIcon },
      { id: 'videos', labelKey: 'browser.msmSuche.tabVideos', icon: Video },
      { id: 'news', labelKey: 'browser.msmSuche.tabNews', icon: Newspaper },
    ],
    [],
  )

  const regionOptionen: DropdownOption[] = useMemo(
    () => [
      { value: 'de', label: t('browser.msmSuche.regionDe') },
      { value: 'en', label: t('browser.msmSuche.regionEn') },
      { value: 'at', label: t('browser.msmSuche.regionAt') },
      { value: 'ch', label: t('browser.msmSuche.regionCh') },
      { value: 'fr', label: t('browser.msmSuche.regionFr') },
      { value: 'es', label: t('browser.msmSuche.regionEs') },
      { value: 'it', label: t('browser.msmSuche.regionIt') },
      { value: 'nl', label: t('browser.msmSuche.regionNl') },
    ],
    [t],
  )

  // Ein Wissens-Panel ohne Titel ist ein leerer Platzhalter: es erscheint nicht,
  // statt eine leere Kachel zu zeigen. Ohne Web-Treffer gibt es keines.
  const wissensPanel = useMemo(() => {
    if (kategorie !== 'web' || stand.art !== 'da' || stand.treffer.length === 0) return null
    const roh = stand.infobox ?? wissenFallback
    return roh && roh.titel.trim() ? roh : null
  }, [kategorie, stand, wissenFallback])
  const fragenListe = useMemo(
    () => (stand.art === 'da' ? kontextFragen(begriff, stand.vorschlaege, stand.treffer) : []),
    [begriff, stand],
  )

  const renderWissensPanel = (kompaktMobil = false) => {
    if (!wissensPanel) return null
    return (
      <div className="msm-card overflow-hidden shadow-panel">
        {/* Visueller Header des Knowledge Panels */}
        <div className="relative flex min-h-[7rem] flex-col justify-between border-b border-outline-variant/40 bg-gradient-to-br from-surface-container-high via-surface-container to-primary-container/35 p-4 sm:p-5">
          {wissensPanel.bild && (
            <SuchBild
              url={wissensPanel.bild}
              alt={wissensPanel.titel}
              className="pointer-events-none absolute right-4 top-4 h-16 w-16 rounded-xl border border-outline-variant/50 bg-surface-container object-cover shadow-md z-10"
            />
          )}
          <div className="relative z-10 flex items-center justify-between gap-2 pr-20">
            <Badge variant="info">{t('browser.msmSuche.wissensgraphBadge')}</Badge>
          </div>
          <div className="relative z-10 mt-3 pr-20">
            <h2 className="text-title-lg font-bold tracking-tight text-on-surface">{wissensPanel.titel}</h2>
            {wissensPanel.untertitel && (
              <p className="mt-0.5 text-label-sm font-medium text-primary">{wissensPanel.untertitel}</p>
            )}
          </div>
        </div>

        {/* Auf Mobilgeräten optional einklappbar für kompakte Übersicht */}
        {kompaktMobil && (
          <div className="border-b border-outline-variant/30 px-4 py-2 lg:hidden">
            <button
              type="button"
              onClick={() => setWissenMobilOffen((v) => !v)}
              className="flex w-full items-center justify-between text-label-sm font-medium text-primary"
            >
              <span>{t(wissenMobilOffen ? 'browser.msmSuche.zusammenfassungEinklappen' : 'browser.msmSuche.steckbriefZeigen')}</span>
              {wissenMobilOffen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
        )}

        <div className={`space-y-4 p-4 sm:p-5 ${kompaktMobil && !wissenMobilOffen ? 'hidden lg:block' : ''}`}>
          <p className="text-body-sm leading-relaxed text-on-surface">
            {wissensPanel.inhalt}
            {wissensPanel.urls && wissensPanel.urls[0] && (
              <button
                type="button"
                onClick={(ev) => zielOeffnen(wissensPanel.urls![0].url, ev.ctrlKey || ev.metaKey)}
                className="ml-1.5 inline-flex items-center gap-1 text-label-sm font-semibold text-primary hover:underline"
              >
                <span>{wissensPanel.urls[0].title || 'Wikipedia'}</span>
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </button>
            )}
          </p>

          {/* Strukturierte Fakten / Eckdaten */}
          {wissensPanel.attribute && wissensPanel.attribute.length > 0 && (
            <div className="space-y-2 border-t border-outline-variant/30 pt-3">
              <div className="text-label-sm font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('browser.msmSuche.steckbriefTitel')}
              </div>
              {wissensPanel.attribute.map((attr, idx) => (
                <div
                  key={`${attr.label}-${idx}`}
                  className="grid grid-cols-12 gap-2 border-b border-outline-variant/20 py-1.5 text-label-sm last:border-0"
                >
                  <span className="col-span-5 font-medium text-on-surface-variant">{attr.label}</span>
                  <span className="col-span-7 font-medium text-on-surface">{attr.value}</span>
                </div>
              ))}
            </div>
          )}

          {/* Weiterführende Links (Offizielle Website, Wikipedia etc.) */}
          {wissensPanel.urls && wissensPanel.urls.length > 1 && (
            <div className="flex flex-wrap gap-2 border-t border-outline-variant/30 pt-3">
              {wissensPanel.urls.map((u) => (
                <Button
                  key={u.url}
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={(ev) => zielOeffnen(u.url, ev.ctrlKey || ev.metaKey)}
                  className="text-label-sm"
                >
                  <span>{u.title}</span>
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                </Button>
              ))}
            </div>
          )}

          {/* Ähnliche Suchanfragen als DNA-Pills im Knowledge Panel */}
          {stand.art === 'da' && stand.vorschlaege.length > 0 && (
            <div className="border-t border-outline-variant/30 pt-3">
              <div className="mb-2.5 text-label-sm font-semibold text-on-surface">
                {t('browser.msmSuche.dazuSuchenNutzer')}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {stand.vorschlaege.slice(0, 6).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => oeffnen(msmSucheAdresse(v), tab.id)}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-outline-variant/40 bg-surface-container px-3 py-1.5 text-label-sm font-medium text-on-surface transition-colors hover:border-primary/40 hover:bg-surface-container-high hover:text-primary"
                  >
                    <Search className="h-3 w-3 text-primary" aria-hidden="true" />
                    <span className="truncate max-w-[200px]">{v}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  let inhalt: React.ReactNode
  if (!begriff) {
    inhalt = <Zustandsflaeche art="leer" titel={t('browser.msmSuche.ohneBegriff')} />
  } else if (stand.art === 'laedt') {
    inhalt = (
      <p role="status" className="flex items-center gap-2 py-8 text-body-sm text-on-surface-variant">
        <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
        {t('browser.msmSuche.laedt')}
      </p>
    )
  } else if (stand.art === 'offline') {
    inhalt = <Zustandsflaeche art="offline" titel={t('browser.msmSuche.offlineTitel')} text={t('browser.msmSuche.offlineText')} />
  } else if (stand.art === 'fehler') {
    inhalt = (
      <Zustandsflaeche
        art="fehler"
        titel={t('browser.msmSuche.fehlerTitel')}
        text={t(stand.zuViele ? 'browser.msmSuche.zuVieleText' : 'browser.msmSuche.fehlerText')}
        erneutLabel={t('browser.msmSuche.erneut')}
        onErneut={() => setVersuch((v) => v + 1)}
      />
    )
  } else if (stand.art === 'nichtEingerichtet') {
    inhalt = (
      <Zustandsflaeche art="leer" titel={t('browser.msmSuche.nichtEingerichtetTitel')} text={t('browser.msmSuche.nichtEingerichtetText')}>
        <Button variant="secondary" onClick={mitDuckDuckGo}>
          {t('browser.msmSuche.mitDuckDuckGo')}
        </Button>
      </Zustandsflaeche>
    )
  } else if (stand.treffer.length === 0 && (kategorie !== 'news' || lokaleNews.length === 0)) {
    inhalt = <Zustandsflaeche art="leer" ansagen titel={t('browser.msmSuche.keineTreffer', { begriff })} />
  } else if (kategorie === 'images') {
    // TAB 2: BILDER-ANSICHT
    inhalt = (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3 animate-fade-in">
        {stand.treffer.map((x, idx) => {
          const domain = host(x.url)
          return (
            <button
              key={`${x.url}-${idx}`}
              type="button"
              onClick={(ev) => zielOeffnen(x.url, ev.ctrlKey || ev.metaKey)}
              onAuxClick={(ev) => ev.button === 1 && zielOeffnen(x.url, true)}
              className="msm-card group flex flex-col overflow-hidden text-left transition-all hover:border-primary/60"
            >
              <div className="relative flex h-44 w-full flex-col justify-between overflow-hidden border-b border-outline-variant/30 bg-gradient-to-br from-surface-container-high via-surface-container to-primary-container/25 p-3.5">
                {x.bild && (
                  <SuchBild
                    url={x.bild}
                    alt={x.titel}
                    className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                  />
                )}
                <div className="relative z-10 flex items-center justify-between">
                  <span className="rounded bg-surface/80 px-2 py-0.5 text-label-sm font-medium text-primary backdrop-blur-sm">
                    {domain}
                  </span>
                </div>
                {!x.bild && (
                  <div className="relative z-10 my-auto flex items-center justify-center">
                    <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-outline-variant/40 bg-surface-container/80 text-primary">
                      <ImageIcon className="h-6 w-6" aria-hidden="true" />
                    </div>
                  </div>
                )}
              </div>
              <div className="p-3.5">
                <p className="line-clamp-2 text-body-sm font-semibold text-on-surface group-hover:text-primary">
                  {x.titel || domain}
                </p>
                <p className="mt-1 truncate text-label-sm text-on-surface-variant">{domain}</p>
              </div>
            </button>
          )
        })}
      </div>
    )
  } else if (kategorie === 'videos') {
    // TAB 3: VIDEOS-ANSICHT
    inhalt = (
      <div className="grid max-w-4xl grid-cols-1 gap-4 md:grid-cols-2 animate-fade-in">
        {stand.treffer.map((x, idx) => {
          const domain = host(x.url)
          return (
            <button
              key={`${x.url}-${idx}`}
              type="button"
              onClick={(ev) => zielOeffnen(x.url, ev.ctrlKey || ev.metaKey)}
              onAuxClick={(ev) => ev.button === 1 && zielOeffnen(x.url, true)}
              className="msm-card group flex flex-col overflow-hidden text-left transition-all hover:border-primary/60"
            >
              <div className="relative flex h-44 w-full flex-col justify-between overflow-hidden border-b border-outline-variant/30 bg-gradient-to-br from-surface-container-high via-surface-container to-primary-container/30 p-4">
                {x.bild && (
                  <SuchBild
                    url={x.bild}
                    alt={x.titel}
                    className="absolute inset-0 h-full w-full object-cover opacity-65 transition-transform duration-300 group-hover:scale-105"
                  />
                )}
                <div className="relative z-10 flex items-center justify-between">
                  <span className="rounded bg-surface/80 px-2 py-0.5 text-label-sm font-semibold text-on-surface backdrop-blur-sm">
                    {domain}
                  </span>
                </div>
                <div className="relative z-10 my-auto flex items-center justify-center">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full border border-primary/50 bg-primary/20 text-primary shadow-md transition-transform group-hover:scale-110">
                    <Play className="h-5 w-5 fill-current ml-0.5" aria-hidden="true" />
                  </div>
                </div>
              </div>
              <div className="p-4">
                <h3 className="line-clamp-2 text-body-md font-semibold text-on-surface group-hover:text-primary">
                  {x.titel || domain}
                </h3>
                {x.inhalt && (
                  <p className="mt-1.5 line-clamp-2 text-label-sm text-on-surface-variant">{x.inhalt}</p>
                )}
              </div>
            </button>
          )
        })}
      </div>
    )
  } else if (kategorie === 'news') {
    // TAB 4: NEWS-ANSICHT
    const newsEintraege = stand.treffer.length > 0 ? stand.treffer : lokaleNews
    inhalt = (
      <div className="max-w-3xl space-y-3.5 animate-fade-in">
        {newsEintraege.map((n, idx) => {
          const domain = host(n.url)
          return (
            <article key={`${n.url}-${idx}`}>
              <button
                type="button"
                onClick={(ev) => zielOeffnen(n.url, ev.ctrlKey || ev.metaKey)}
                onAuxClick={(ev) => ev.button === 1 && zielOeffnen(n.url, true)}
                className="msm-card group flex w-full flex-col gap-1.5 p-4 sm:p-5 text-left transition-all hover:border-primary/60"
              >
                <div className="flex items-center gap-2 text-label-sm text-on-surface-variant">
                  <span className="font-semibold text-primary">{domain}</span>
                </div>
                <h3 className="text-title-md font-semibold text-on-surface group-hover:text-primary group-hover:underline">
                  {n.titel || domain}
                </h3>
                {n.inhalt && (
                  <p className="line-clamp-2 sm:line-clamp-3 text-body-sm leading-relaxed text-on-surface-variant">
                    {n.inhalt}
                  </p>
                )}
              </button>
            </article>
          )
        })}
      </div>
    )
  } else {
    // TAB 1: WEB-SUCHE (2-SPALTEN ENTERPRISE LAYOUT, 7 / 5)
    inhalt = (
      <div className="grid grid-cols-1 items-start gap-7 lg:grid-cols-12 animate-fade-in">
        {/* LINKE HAUPTSPALTE (7 von 12 Spalten) */}
        <div className="space-y-5 lg:col-span-7">
          {/* A. SINGRA AI ÜBERBLICK BOX */}
          <SuchzusammenfassungKachel
            begriff={begriff}
            treffer={stand.treffer}
            vorschlaege={stand.vorschlaege}
            onNeueTreffer={onNeueTreffer}
            onOeffnen={zielOeffnen}
          />

          {/* Auf Mobilgeräten / Tablets reiht sich das Wikipedia-Knowledge-Panel kompakt unter der KI-Box ein */}
          {wissensPanel && <div className="lg:hidden">{renderWissensPanel(true)}</div>}

          {/* B. KONTEXTBEZOGENE "WEITERE FRAGEN ZUM THEMA" — nur, wenn die Suche
              echte Vorschläge oder Treffer mit Text geliefert hat. */}
          {fragenListe.length > 0 && (
            <section
              className="msm-card p-4 sm:p-5"
              aria-label={t('browser.msmSuche.weitereFragen')}
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="text-title-sm font-semibold text-on-surface">
                  {t('browser.msmSuche.weitereFragen')}
                </h2>
                <span className="text-label-sm text-on-surface-variant">
                  {t('browser.msmSuche.kontextGeneriert')}
                </span>
              </div>
              <div className="space-y-2">
                {fragenListe.map((item, fIdx) => (
                  <details
                    key={fIdx}
                    name="weitere-fragen"
                    open={fIdx === 0}
                    className="group overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container-low/80 transition-colors hover:bg-surface-container"
                  >
                    <summary className="flex cursor-pointer list-none select-none items-center justify-between px-4 py-3 text-body-sm font-medium text-on-surface">
                      <span>{item.frage}</span>
                      <ChevronDown
                        className="ml-3 h-4 w-4 shrink-0 text-on-surface-variant transition-transform duration-200 group-open:rotate-180 group-open:text-primary"
                        aria-hidden="true"
                      />
                    </summary>
                    <div className="border-t border-outline-variant/25 px-4 pb-3.5 pt-2.5 text-body-sm leading-relaxed text-on-surface-variant">
                      {item.antwort && <p className="mb-2.5 line-clamp-3">{item.antwort}</p>}
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => oeffnen(msmSucheAdresse(item.frage), tab.id)}
                      >
                        <Search className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                        <span>{item.frage}</span>
                      </Button>
                    </div>
                  </details>
                ))}
              </div>
            </section>
          )}

          {/* C. ORGANISCHE WEB-ERGEBNISSE (CLEAN CARDS OHNE KAPUTTE BILDER) */}
          <ol className="space-y-3.5" aria-label={t('browser.msmSuche.trefferListe', { begriff })}>
            {stand.treffer.map((x, idx) => {
              const domain = host(x.url)
              const breadcrumb = pfad(x.url)
              // Beim Top-Ergebnis unterstützen wir Sitelinks / Unterseiten-Pills, sofern Vorschläge vorliegen
              const sitelinks = idx === 0 ? stand.vorschlaege.slice(0, 4) : []

              return (
                <li key={x.url} id={`treffer-${idx + 1}`} className="group">
                  <article className="msm-card p-4 sm:p-5 transition-colors hover:ring-1 hover:ring-primary/30">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <button
                          type="button"
                          onClick={(ev) => zielOeffnen(x.url, ev.ctrlKey || ev.metaKey)}
                          onAuxClick={(ev) => ev.button === 1 && zielOeffnen(x.url, true)}
                          className="flex w-full min-w-0 flex-col text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary rounded-lg"
                        >
                          {/* Favicon + Seitenname + Breadcrumb */}
                          <div className="mb-1.5 flex items-center gap-2.5">
                            <div className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full border border-outline-variant/40 bg-surface-container-high text-label-sm font-bold text-primary">
                              <SeitenSymbol url={x.url} host={domain} />
                            </div>
                            <div className="min-w-0 truncate">
                              <div className="truncate text-label-sm font-medium leading-tight text-on-surface">
                                {domain}
                              </div>
                              <div className="truncate text-label-sm text-on-surface-variant/80">
                                {domain}
                                {breadcrumb}
                              </div>
                            </div>
                          </div>

                          {/* Klickbarer Titel */}
                          <h3 className="mt-1 text-title-md font-semibold leading-snug text-primary group-hover:underline line-clamp-2">
                            {x.titel || domain}
                          </h3>

                          {/* Beschreibungstext (Snippet mit Mobile-Line-Clamp) */}
                          {x.inhalt && (
                            <p className="mt-1.5 line-clamp-2 sm:line-clamp-3 text-body-sm leading-relaxed text-on-surface-variant">
                              {x.inhalt}
                            </p>
                          )}
                        </button>

                        {/* Sitelinks beim Top-Ergebnis */}
                        {sitelinks.length > 0 && (
                          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-outline-variant/25 pt-2.5">
                            {sitelinks.map((link) => (
                              <button
                                key={link}
                                type="button"
                                onClick={() => oeffnen(msmSucheAdresse(link), tab.id)}
                                className="rounded-lg border border-outline-variant/40 bg-surface-container-low px-2.5 py-1 text-label-sm font-medium text-primary transition-colors hover:border-primary/40 hover:bg-surface-container-high"
                              >
                                {link} ↗
                              </button>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Rechtsseitiges Vorschaubild (nur wenn fehlerfrei über den eigenen Proxy ladbar) */}
                      {x.bild && (
                        <SuchBild
                          url={x.bild}
                          alt=""
                          className="hidden sm:block h-24 w-32 shrink-0 rounded-xl border border-outline-variant/40 bg-surface-container-high object-cover"
                        />
                      )}
                    </div>
                  </article>
                </li>
              )
            })}
          </ol>

          {/* D. ÄHNLICHE SUCHANFRAGEN (2-SPALTEN-GRID) */}
          {stand.vorschlaege.length > 0 && (
            <section
              className="msm-card p-4 sm:p-5"
              aria-label={t('browser.msmSuche.oftGesucht')}
            >
              <div className="mb-3.5 flex items-center justify-between gap-2">
                <h2 className="text-title-sm font-semibold text-on-surface">
                  {t('browser.msmSuche.oftGesucht')}
                </h2>
                <span className="text-label-sm text-on-surface-variant">
                  {t('browser.msmSuche.kontextVorschlaege')}
                </span>
              </div>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {stand.vorschlaege.map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => oeffnen(msmSucheAdresse(v), tab.id)}
                    className="group flex items-center justify-between rounded-xl border border-outline-variant/40 bg-surface-container-low px-3.5 py-2.5 text-left text-label-sm font-medium text-on-surface transition-colors hover:border-primary/40 hover:bg-surface-container-high hover:text-primary"
                  >
                    <div className="flex min-w-0 items-center gap-2.5">
                      <Search className="h-3.5 w-3.5 shrink-0 text-on-surface-variant group-hover:text-primary" aria-hidden="true" />
                      <span className="truncate">{v}</span>
                    </div>
                    <span className="ml-2 text-on-surface-variant group-hover:text-primary" aria-hidden="true">
                      →
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* E. SEITENNAVIGATION (PAGINIERUNG IM DNA-DESIGN) */}
          <nav
            className="flex flex-wrap items-center justify-center gap-1.5 pt-2 pb-4 sm:justify-start"
            aria-label={t('common.pagination.label')}
          >
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={stand.weitereLaedt || stand.seite <= 1}
              onClick={() => seiteWechseln(stand.seite - 1)}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              <span>{t('common.back')}</span>
            </Button>

            {[1, 2, 3, 4].map((nr) => (
              <Button
                key={nr}
                type="button"
                variant={stand.seite === nr ? 'primary' : 'secondary'}
                size="sm"
                disabled={stand.weitereLaedt}
                onClick={() => seiteWechseln(nr)}
                className="w-9 px-0"
              >
                {nr}
              </Button>
            ))}

            {stand.mehr && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={weitere}
                disabled={stand.weitereLaedt}
              >
                {stand.weitereLaedt && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                <span>{t('browser.msmSuche.weitere')}</span>
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </nav>
        </div>

        {/* RECHTE KONTEXT-SPALTE: WIKIPEDIA / KNOWLEDGE PANEL (5 von 12 Spalten) */}
        <aside className="hidden space-y-5 lg:col-span-5 lg:sticky lg:top-28 lg:block">
          {wissensPanel && renderWissensPanel(false)}

          {/* Aktuelle Schlagzeiten zum gesuchten Thema, unter dem Wissens-Panel */}
          {lokaleNews.length > 0 && (
            <div
              className="msm-card p-5"
              aria-label={t('browser.msmSuche.aktuelleNews')}
            >
              <div className="mb-3 flex items-center gap-2 border-b border-outline-variant/30 pb-2.5">
                <Newspaper className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-title-sm font-semibold text-on-surface">
                  {t('browser.msmSuche.aktuelleNews')}
                </h2>
              </div>
              <div className="space-y-3">
                {lokaleNews.slice(0, 4).map((item, idx) => (
                  <div key={idx} className="border-b border-outline-variant/20 pb-2.5 last:border-b-0 last:pb-0">
                    <button
                      type="button"
                      onClick={(ev) => zielOeffnen(item.url, ev.ctrlKey || ev.metaKey)}
                      className="group w-full text-left"
                    >
                      <span className="mb-0.5 block truncate text-label-sm text-on-surface-variant">
                        {host(item.url)}
                      </span>
                      <span className="line-clamp-2 text-body-sm font-medium leading-snug text-on-surface group-hover:text-primary group-hover:underline">
                        {item.titel}
                      </span>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background text-on-surface">
      <h1 className="sr-only">{t('browser.msmSuche.titel', { begriff })}</h1>

      {/* 1. STICKY SUCHMASCHINEN-HEADER (MAUNTING SEARCH) */}
      <header className="sticky top-0 z-30 border-b border-outline-variant/40 bg-background/95 px-4 pt-3.5 backdrop-blur-md sm:px-6">
        <div className="mx-auto max-w-[1360px]">
          {/* Obere Header-Zeile: MSB-Globus-Logo + Maunting Search + DNA-Suchleiste + Regionsauswahl.
              Ein einziges Region-Dropdown im DOM (keine doppelte Steuerung): am Handy rechts
              neben dem Logo, am Rechner rechts außen hinter der Suchleiste. */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => startseite(tab.id)}
              className="group order-1 flex shrink-0 items-center gap-2 text-left"
            >
              <img
                src="/msp.png"
                alt=""
                className="h-8 w-8 shrink-0 rounded-full border border-primary/40 shadow-sm transition-transform group-hover:scale-105"
              />
              <div className="flex items-baseline gap-1.5 leading-none">
                <span className="text-title-md font-semibold tracking-tight text-on-surface">Maunting</span>
                <span className="text-title-md font-medium tracking-tight text-primary">Search</span>
              </div>
            </button>

            {/* Regionsauswahl: Mobil neben dem Logo, Desktop rechts außen */}
            <div className="order-2 w-36 shrink-0 md:order-5 md:ml-auto md:w-44">
              <Dropdown
                value={region}
                onChange={regionWaehlen}
                options={regionOptionen}
                align="end"
                buttonClassName="h-8 px-2.5 text-label-sm md:h-9 md:px-3"
                aria-label={t('browser.msmSuche.regionLabel')}
              />
            </div>

            {/* Wiederverwendete DNA-Suchleiste mit Live-Autocomplete */}
            <div className="order-3 w-full min-w-0 md:order-2 md:max-w-[690px] md:flex-1">
              <SuchEingabeLeiste
                initialWert={begriff}
                onSuchen={(neu) => oeffnen(msmSucheAdresse(neu), tab.id)}
                kompakt
                autocomplete={!tab.privat}
                sprache={region}
              />
            </div>
          </div>

          {/* Untere Header-Zeile: Kategorie-Tabs (Web, Bilder, Videos, News) & Treffer-Statistik */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 pb-2 md:pl-[211px]">
            <TabBar
              tabs={kategorieTabs}
              active={kategorie}
              onChange={setKategorie}
              ariaLabel={t('browser.msmSuche.kategorienLabel')}
              embedded
              einzeilig
              kompakt
            />

            {stand.art === 'da' && stand.treffer.length > 0 && (
              <span className="hidden text-label-sm text-on-surface-variant sm:inline">
                {t('browser.msmSuche.trefferStatistik', {
                  anzahl: stand.treffer.length,
                  dauer: stand.dauerSek.toFixed(2).replace('.', ','),
                })}
              </span>
            )}
          </div>
        </div>
      </header>

      {/* 2. HAUPTINHALT (kein zweites <main>: die Fläche des Browsers ist bereits eins) */}
      <div className="mx-auto w-full max-w-[1360px] flex-1 px-4 py-6 sm:px-6 md:pl-[211px]">
        {inhalt}
      </div>

      {/* 3. TRANSPARENTER ENTERPRISE FOOTER (AGPLv3-Hinweis) */}
      <footer className="mt-auto border-t border-outline-variant/30 bg-surface-container-lowest px-4 py-4 text-label-sm text-on-surface-variant sm:px-6">
        <div className="mx-auto flex max-w-[1360px] flex-col items-center justify-between gap-2 sm:flex-row md:pl-[187px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-on-surface">Maunting Search</span>
            <span aria-hidden="true">•</span>
            <span>{t('browser.msmSuche.footerKi')}</span>
            <span aria-hidden="true">•</span>
            <span>{t('browser.msmSuche.footerLizenz')}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span>{t('browser.msmSuche.footerDienst')}</span>
          </div>
        </div>
      </footer>
    </div>
  )
}

/**
 * Der gemerkte Rückweg (`vorher`) kennt keine Webview: von einem Treffer zur
 * Suche, von der Suche zur Seite davor. Unter Android landet die Zurück-Taste
 * dann in der Oberfläche; ein Verlaufseintrag dort geht diesen Weg, statt die
 * App zu beenden.
 */
export function useZurueckZurSuche(tab: Tab | undefined) {
  const aktion = useTabsStore((s) => s.aktion)
  const id = tab?.id
  useZurueckSchliesst(!!tab && !tab.zurueck && !!tab.vorher, () => {
    if (id) aktion('zurueck', id)
  })
}
