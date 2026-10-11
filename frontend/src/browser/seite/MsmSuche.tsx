/**
 * Die Trefferseite der MSM-Suche (`msb://suche?q=…`). Die Treffer kommen vom
 * eigenen Server (`POST /api/browser/suche`), der über seine SearXNG-Instanz
 * sucht.
 *
 * Die Seite bietet ein modernes Suchergebnis-Erlebnis im Google-Stil:
 * - KI-Überblick oben mit Quellen-Verweisen, Disclaimer und Kopierfunktion
 * - Strukturierte Ergebnis-Karten mit Favicon-Symbol, Host, Breadcrumbs und Auszug
 * - Aufklappbare Anschlussfragen („Weitere Fragen zum Thema“)
 * - Suchvorschläge („Wird auch oft gesucht“) aus SearXNG
 * - Optionale News-Sidebar mit aktuellen Schlagzeilen
 */
import { useEffect, useState } from 'react'
import { ChevronDown, Loader2, Newspaper, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api, isNetworkOrOfflineError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { useZurueckSchliesst } from '@/Singra/UI/useZurueckSchliesst'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { NACHRICHTEN_THEMEN } from '../services/einstellungenStore'
import { msmSucheAdresse, msmSucheBegriff } from '../services/intern'
import { nativ, type SchutzStand } from '../services/nativ'
import { suchAdresse } from '../services/searchEngines'
import { useTabsStore, type Tab } from '../services/tabsStore'
import { KachelSymbol } from './start/KachelSymbol'
import { auswahl, useNachrichten } from './start/nachrichtenDaten'
import { SuchzusammenfassungKachel } from './suchzusammenfassung/SuchzusammenfassungKachel'

export interface Treffer {
  titel: string
  url: string
  inhalt: string
  bild?: string
}

type Stand =
  | { art: 'laedt' }
  | { art: 'da'; treffer: Treffer[]; vorschlaege: string[]; seite: number; mehr: boolean; weitereLaedt: boolean }
  | { art: 'fehler'; zuViele: boolean }
  | { art: 'offline' }
  | { art: 'nichtEingerichtet' }

/** Zuletzt gesehene Treffer, damit Zurück nicht noch einmal sucht. Nur im Speicher. */
const GEMERKT_MAX = 10
const gemerkt = new Map<string, Extract<Stand, { art: 'da' }>>()

function merken(url: string, stand: Extract<Stand, { art: 'da' }>) {
  gemerkt.delete(url)
  gemerkt.set(url, { ...stand, weitereLaedt: false })
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
    return ' > ' + teile.slice(0, 3).join(' > ')
  } catch {
    return ''
  }
}

function baueWeitereFragen(begriff: string, sprache: string): string[] {
  if (sprache === 'de') {
    return [
      `Was sind die wichtigsten Details zu „${begriff}“?`,
      `Welche aktuellen Entwicklungen gibt es zu „${begriff}“?`,
      `Was sollte man über „${begriff}“ wissen?`,
    ]
  }
  return [
    `What are the key details about “${begriff}”?`,
    `What are current developments regarding “${begriff}”?`,
    `What should you know about “${begriff}”?`,
  ]
}

async function holen(begriff: string, seite: number, sprache: string): Promise<{ treffer: Treffer[]; vorschlaege: string[] }> {
  const sicher = sichereSuche(await nativ.schutzStand().catch(() => null))
  const antwort = await api<{ treffer: Treffer[]; vorschlaege?: string[] }>('/browser/suche', {
    method: 'POST',
    body: JSON.stringify({ q: begriff, seite, sicher, sprache }),
  })
  return { treffer: antwort.treffer, vorschlaege: antwort.vorschlaege ?? [] }
}

export function MsmSuche({ tab }: { tab: Tab }) {
  const { t, i18n } = useTranslation()
  const oeffnen = useTabsStore((s) => s.oeffnen)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const begriff = msmSucheBegriff(tab.url) ?? ''
  const sprache = i18n.language.slice(0, 2)
  const [stand, setStand] = useState<Stand>(() => gemerkt.get(tab.url) ?? { art: 'laedt' })
  const [versuch, setVersuch] = useState(0)

  // Aktuelle News aus dem lokalen Nachrichten-Spiegel/Cache
  const { stand: nachrichtenStand } = useNachrichten()
  const news = nachrichtenStand ? auswahl(nachrichtenStand, NACHRICHTEN_THEMEN, begriff, 4) : []

  useEffect(() => {
    const schon = gemerkt.get(tab.url)
    if (schon && versuch === 0) {
      setStand(schon)
      return
    }
    let aktiv = true
    setStand({ art: 'laedt' })
    holen(begriff, 1, sprache).then(
      ({ treffer, vorschlaege }) => {
        if (!aktiv) return
        const neu = { art: 'da' as const, treffer, vorschlaege, seite: 1, mehr: treffer.length > 0, weitereLaedt: false }
        merken(tab.url, neu)
        setStand(neu)
      },
      (fehler: unknown) => {
        if (!aktiv) return
        const code = (fehler as { code?: string | null })?.code
        // Ein Code vom Panel heißt: das Netz ist da. 502 hieße sonst „offline“.
        if (code === 'BROWSER_SUCHE_NICHT_EINGERICHTET') setStand({ art: 'nichtEingerichtet' })
        else if (!code?.startsWith('BROWSER_SUCHE_') && isNetworkOrOfflineError(fehler)) setStand({ art: 'offline' })
        else setStand({ art: 'fehler', zuViele: code === 'BROWSER_SUCHE_ZU_VIELE' })
      },
    )
    return () => {
      aktiv = false
    }
  }, [tab.url, begriff, sprache, versuch])

  // Ohne Netz lädt die Seite von selbst, sobald es zurück ist.
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
    holen(begriff, bisher.seite + 1, sprache).then(
      ({ treffer: neu, vorschlaege: neueVorschlaege }) => {
        const bekannt = new Set(bisher.treffer.map((x) => x.url))
        const dazu = neu.filter((x) => !bekannt.has(x.url))
        const kombiniertVorschlaege = Array.from(new Set([...bisher.vorschlaege, ...neueVorschlaege]))
        const naechster = {
          ...bisher,
          treffer: [...bisher.treffer, ...dazu],
          vorschlaege: kombiniertVorschlaege,
          seite: bisher.seite + 1,
          mehr: dazu.length > 0,
          weitereLaedt: false,
        }
        merken(tab.url, naechster)
        setStand(naechster)
      },
      () => setStand({ ...bisher, weitereLaedt: false, mehr: true }),
    )
  }

  const onNeueTreffer = (neue: Treffer[]) => {
    setStand((prev) => {
      if (prev.art !== 'da') return prev
      const bekannte = new Set(prev.treffer.map((x) => x.url))
      const dazu = neue.filter((x) => !bekannte.has(x.url))
      if (dazu.length === 0) return prev
      const aktualisiert = { ...prev, treffer: [...prev.treffer, ...dazu] }
      merken(tab.url, aktualisiert)
      return aktualisiert
    })
  }

  const mitDuckDuckGo = () => {
    const ziel = suchAdresse(begriff, 'duckduckgo')
    if (ziel) oeffnen(ziel, tab.id)
  }

  let inhalt: React.ReactNode
  if (!begriff) {
    inhalt = <Zustandsflaeche art="leer" titel={t('browser.msmSuche.ohneBegriff')} />
  } else if (stand.art === 'laedt') {
    inhalt = (
      <p role="status" className="flex items-center gap-2 text-body-sm text-on-surface-variant">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
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
  } else if (stand.treffer.length === 0) {
    inhalt = <Zustandsflaeche art="leer" ansagen titel={t('browser.msmSuche.keineTreffer', { begriff })} />
  } else {
    inhalt = (
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-12">
        {/* Hauptspalte */}
        <div className="col-span-12 lg:col-span-8 flex flex-col gap-5">
          {/* KI-Zusammenfassung oben */}
          <SuchzusammenfassungKachel begriff={begriff} treffer={stand.treffer} onNeueTreffer={onNeueTreffer} />

          {/* Weitere Fragen (People Also Ask) */}
          <section className="rounded-2xl border border-outline-variant/30 bg-surface-container-low/70 p-4" aria-label={t('browser.msmSuche.weitereFragen')}>
            <h2 className="mb-3 text-title-sm font-semibold text-on-surface">
              {t('browser.msmSuche.weitereFragen')}
            </h2>
            <div className="space-y-2">
              {baueWeitereFragen(begriff, sprache).map((frage, fIdx) => (
                <details
                  key={fIdx}
                  name="weitere-fragen"
                  className="group rounded-xl border border-outline-variant/40 bg-surface-container-low/80 p-3 transition-colors hover:bg-surface-container"
                >
                  <summary className="flex cursor-pointer list-none items-center justify-between font-medium text-body-sm text-on-surface">
                    <span>{frage}</span>
                    <ChevronDown className="h-4 w-4 text-on-surface-variant transition-transform duration-200 group-open:rotate-180" />
                  </summary>
                  <div className="mt-2.5 border-t border-outline-variant/20 pt-2.5 text-body-sm text-on-surface-variant">
                    <p className="mb-2 leading-relaxed">
                      {t('browser.msmSuche.weitereFragenHinweis')}
                    </p>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => oeffnen(msmSucheAdresse(frage), tab.id)}
                    >
                      {frage}
                    </Button>
                  </div>
                </details>
              ))}
            </div>
          </section>

          {/* Organische Treffer (Google-Style Cards) */}
          <ol className="flex flex-col gap-3" aria-label={t('browser.msmSuche.trefferListe', { begriff })}>
            {stand.treffer.map((x, idx) => (
              <li key={x.url} id={`treffer-${idx + 1}`} className="group">
                <button
                  type="button"
                  onClick={(ev) => (ev.ctrlKey || ev.metaKey ? neuerTab(x.url, { nach: tab.id, privat: tab.privat, hintergrund: true }) : oeffnen(x.url, tab.id))}
                  onAuxClick={(ev) => ev.button === 1 && neuerTab(x.url, { nach: tab.id, privat: tab.privat, hintergrund: true })}
                  className="flex w-full min-w-0 flex-col gap-1.5 rounded-2xl border border-outline-variant/30 bg-surface-container-low/60 p-4 text-left transition-all hover:border-outline-variant hover:bg-surface-container hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                >
                  {/* Kopfzeile: Favicon, Domain & Breadcrumbs */}
                  <div className="flex items-center gap-2 text-label-sm text-on-surface-variant">
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-surface-container-high text-label-sm font-semibold text-primary">
                      <KachelSymbol url={x.url} host={host(x.url)} />
                    </div>
                    <span className="font-medium text-on-surface truncate">{host(x.url)}</span>
                    <span className="truncate text-on-surface-variant/70 text-label-sm">{pfad(x.url)}</span>
                  </div>

                  {/* Titel */}
                  <span className="text-title-md font-semibold text-primary group-hover:underline line-clamp-2">
                    {x.titel || host(x.url)}
                  </span>

                  {/* Inhalt & optionales Thumbnail */}
                  <div className="flex items-start justify-between gap-3">
                    {x.inhalt && (
                      <span className="line-clamp-3 text-body-sm text-on-surface-variant leading-relaxed flex-1">
                        {x.inhalt}
                      </span>
                    )}
                    {x.bild && (
                      <img
                        src={x.bild}
                        alt=""
                        loading="lazy"
                        className="h-16 w-20 shrink-0 rounded-lg border border-outline-variant/20 object-cover bg-surface-container-high"
                      />
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ol>

          {/* Wird oft gesucht (Related Searches) */}
          {stand.vorschlaege.length > 0 && (
            <section className="mt-2 rounded-2xl border border-outline-variant/30 bg-surface-container-low/70 p-4" aria-label={t('browser.msmSuche.oftGesucht')}>
              <div className="flex items-center gap-2 mb-3">
                <Search className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-title-sm font-semibold text-on-surface">
                  {t('browser.msmSuche.oftGesucht')}
                </h2>
              </div>
              <div className="flex flex-wrap gap-2">
                {stand.vorschlaege.map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => oeffnen(msmSucheAdresse(v), tab.id)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-outline-variant/40 bg-surface-container px-3.5 py-1.5 text-label-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high hover:border-primary/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                  >
                    <Search className="h-3 w-3 text-on-surface-variant" aria-hidden="true" />
                    <span>{v}</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Mehr Ergebnisse laden */}
          {stand.mehr && (
            <Button variant="secondary" className="self-center mt-2" onClick={weitere} disabled={stand.weitereLaedt}>
              {stand.weitereLaedt && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {t('browser.msmSuche.weitere')}
            </Button>
          )}
        </div>

        {/* Rechte Sidebar (News / Schlagzeilen) */}
        {news.length > 0 && (
          <aside className="col-span-12 lg:col-span-4" aria-label={t('browser.msmSuche.aktuelleNews')}>
            <div className="rounded-2xl border border-outline-variant/30 bg-surface-container-low/80 p-4 sticky top-4">
              <div className="flex items-center gap-2 mb-3 border-b border-outline-variant/20 pb-2">
                <Newspaper className="h-4 w-4 text-primary" aria-hidden="true" />
                <h2 className="text-title-sm font-semibold text-on-surface">
                  {t('browser.msmSuche.aktuelleNews')}
                </h2>
              </div>
              <div className="space-y-3">
                {news.map((item, idx) => (
                  <div key={idx} className="border-b border-outline-variant/20 pb-2.5 last:border-b-0 last:pb-0">
                    <button
                      type="button"
                      onClick={(ev) => (ev.ctrlKey || ev.metaKey ? neuerTab(item.url, { nach: tab.id, privat: tab.privat, hintergrund: true }) : oeffnen(item.url, tab.id))}
                      className="text-left w-full group"
                    >
                      <span className="block text-label-sm text-on-surface-variant mb-0.5 truncate">{host(item.url)}</span>
                      <span className="block text-body-sm font-medium text-on-surface group-hover:text-primary group-hover:underline line-clamp-2 leading-snug">
                        {item.titel}
                      </span>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </aside>
        )}
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6">
        <h1 className="sr-only">{t('browser.msmSuche.titel', { begriff })}</h1>
        <p className="px-1 text-label-sm text-on-surface-variant">{t('browser.msmSuche.herkunft')}</p>
        {inhalt}
      </div>
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
