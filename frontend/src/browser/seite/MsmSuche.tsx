/**
 * Die Trefferseite der MSM-Suche (`msb://suche?q=…`). Die Treffer kommen vom
 * eigenen Server (`POST /api/browser/suche`), der über seine SearXNG-Instanz
 * sucht. Gezeigt werden nur Titel, Host und Text, keine Bilder und keine
 * Favicons: die Seite lädt nichts von fremden Servern. Ein Treffer öffnet sich
 * im selben Tab, Zurück führt hierher (`vorher` am Tab).
 *
 * Ist der Jugend- und Suchtschutz für Erwachsenen-Inhalte an oder nicht zu
 * lesen, verlangt die Seite die strenge sichere Suche.
 */
import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api, isNetworkOrOfflineError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { useZurueckSchliesst } from '@/Singra/UI/useZurueckSchliesst'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { msmSucheBegriff } from '../services/intern'
import { nativ, type SchutzStand } from '../services/nativ'
import { suchAdresse } from '../services/searchEngines'
import { useTabsStore, type Tab } from '../services/tabsStore'

export interface Treffer {
  titel: string
  url: string
  inhalt: string
}

type Stand =
  | { art: 'laedt' }
  | { art: 'da'; treffer: Treffer[]; seite: number; mehr: boolean; weitereLaedt: boolean }
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

async function holen(begriff: string, seite: number, sprache: string): Promise<Treffer[]> {
  const sicher = sichereSuche(await nativ.schutzStand().catch(() => null))
  const antwort = await api<{ treffer: Treffer[] }>('/browser/suche', {
    method: 'POST',
    body: JSON.stringify({ q: begriff, seite, sicher, sprache }),
  })
  return antwort.treffer
}

export function MsmSuche({ tab }: { tab: Tab }) {
  const { t, i18n } = useTranslation()
  const oeffnen = useTabsStore((s) => s.oeffnen)
  const neuerTab = useTabsStore((s) => s.neuerTab)
  const begriff = msmSucheBegriff(tab.url) ?? ''
  const sprache = i18n.language.slice(0, 2)
  const [stand, setStand] = useState<Stand>(() => gemerkt.get(tab.url) ?? { art: 'laedt' })
  const [versuch, setVersuch] = useState(0)

  useEffect(() => {
    const schon = gemerkt.get(tab.url)
    if (schon && versuch === 0) {
      setStand(schon)
      return
    }
    let aktiv = true
    setStand({ art: 'laedt' })
    holen(begriff, 1, sprache).then(
      (treffer) => {
        if (!aktiv) return
        const neu = { art: 'da' as const, treffer, seite: 1, mehr: treffer.length > 0, weitereLaedt: false }
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
      (neu) => {
        const bekannt = new Set(bisher.treffer.map((x) => x.url))
        const dazu = neu.filter((x) => !bekannt.has(x.url))
        const naechster = { ...bisher, treffer: [...bisher.treffer, ...dazu], seite: bisher.seite + 1, mehr: dazu.length > 0, weitereLaedt: false }
        merken(tab.url, naechster)
        setStand(naechster)
      },
      () => setStand({ ...bisher, weitereLaedt: false, mehr: true }),
    )
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
      <>
        <ol className="flex flex-col gap-1" aria-label={t('browser.msmSuche.trefferListe', { begriff })}>
          {stand.treffer.map((x) => (
            <li key={x.url}>
              <button
                type="button"
                onClick={(ev) => (ev.ctrlKey || ev.metaKey ? neuerTab(x.url, { nach: tab.id, privat: tab.privat, hintergrund: true }) : oeffnen(x.url, tab.id))}
                onAuxClick={(ev) => ev.button === 1 && neuerTab(x.url, { nach: tab.id, privat: tab.privat, hintergrund: true })}
                className="flex w-full min-w-0 flex-col gap-0.5 rounded-lg px-3 py-2.5 text-left hover:bg-surface-container-high focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
              >
                <span className="max-w-full truncate text-label-sm text-on-surface-variant">{host(x.url)}</span>
                <span className="max-w-full truncate text-title-sm text-primary">{x.titel || host(x.url)}</span>
                {x.inhalt && <span className="line-clamp-2 max-w-full text-body-sm text-on-surface-variant">{x.inhalt}</span>}
              </button>
            </li>
          ))}
        </ol>
        {stand.mehr && (
          <Button variant="secondary" className="self-center" onClick={weitere} disabled={stand.weitereLaedt}>
            {stand.weitereLaedt && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {t('browser.msmSuche.weitere')}
          </Button>
        )}
      </>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
        <h1 className="sr-only">{t('browser.msmSuche.titel', { begriff })}</h1>
        <p className="px-3 text-label-sm text-on-surface-variant">{t('browser.msmSuche.herkunft')}</p>
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
