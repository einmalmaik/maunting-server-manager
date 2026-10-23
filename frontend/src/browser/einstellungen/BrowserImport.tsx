/**
 * Ein Klick, ein Browser: Lesezeichen mit Ordnern, Verlauf, Top-Sites und
 * Symbole aus einem installierten Windows-Browser übernehmen.
 *
 * Rust liest nur Kopien und liefert normalisierte Daten; hier werden sie in
 * die vorhandenen Ablagen zusammengeführt. Der Quell-Browser darf offen
 * bleiben, Passwörter und Karten werden nicht angefasst.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Spinner } from '@/Singra/UI'

import { kachelnNeuLaden } from '../seite/start/KachelSymbol'
import { useBildStore } from '../services/bildStore'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { bereinigeBilder, belegterPlatz, herkunft, kappeAufBudgets } from '../services/importDaten'
import { nativ, type BrowserProfil, type ImportErgebnis } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { useVerlaufStore } from '../services/verlaufStore'
import { Abschnitt, Aktionszeile, Auswahlzeile } from './bausteine'

interface Uebernahme {
  browser: string
  profil: string
  dauer: number
  lesezeichen: number
  verlauf: number
  topSites: number
  favicons: number
  warnungen: string[]
}

function istProfil(wert: unknown): wert is BrowserProfil {
  if (!wert || typeof wert !== 'object') return false
  const profil = wert as Record<string, unknown>
  return (
    typeof profil.id === 'string' &&
    typeof profil.browser === 'string' &&
    typeof profil.name === 'string' &&
    typeof profil.verlauf === 'boolean' &&
    typeof profil.lesezeichen === 'boolean'
  )
}

function codeAus(fehler: unknown): string {
  if (typeof fehler === 'string' && fehler) return fehler
  if (fehler && typeof fehler === 'object' && 'code' in fehler) {
    const code = (fehler as { code?: unknown }).code
    if (typeof code === 'string' && code) return code
  }
  return 'generisch'
}

export function BrowserImport() {
  const { t } = useTranslation()
  const [profile, setProfile] = useState<BrowserProfil[] | null>(null)
  const [ladefehler, setLadefehler] = useState(false)
  const [gewaehlt, setGewaehlt] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const [ergebnis, setErgebnis] = useState<Uebernahme | null>(null)
  const [importfehler, setImportfehler] = useState<string | null>(null)

  const laden = useCallback(() => {
    setProfile(null)
    setLadefehler(false)
    nativ
      .browserProfile()
      .then((liste) => {
        const sauber = Array.isArray(liste) ? liste.filter(istProfil) : []
        setProfile(sauber)
        setGewaehlt((alt) => (sauber.some((p) => p.id === alt) ? alt : (sauber[0]?.id ?? '')))
      })
      .catch(() => {
        setProfile([])
        setLadefehler(true)
      })
  }, [])

  useEffect(() => {
    laden()
  }, [laden])

  if (istAndroid()) return null

  const browserTexte: Record<string, string> = {
    chrome: t('browser.einstellungen.import.browser.chrome'),
    brave: t('browser.einstellungen.import.browser.brave'),
    edge: t('browser.einstellungen.import.browser.edge'),
    vivaldi: t('browser.einstellungen.import.browser.vivaldi'),
    opera: t('browser.einstellungen.import.browser.opera'),
    opera_gx: t('browser.einstellungen.import.browser.opera_gx'),
    firefox: t('browser.einstellungen.import.browser.firefox'),
  }
  const browserName = (code: string) => browserTexte[code] ?? code

  const uebernehmen = (roh: ImportErgebnis): Uebernahme => {
    const angepasst = kappeAufBudgets(roh.lesezeichen, roh.verlauf, belegterPlatz())
    const bilder = bereinigeBilder(roh.bilder)
    const favicons = useBildStore.getState().ergaenzen(bilder)
    const verlauf = useVerlaufStore.getState().importieren({
      lesezeichen: angepasst.lesezeichen,
      verlauf: angepasst.verlauf,
    })
    const topSites = useEinstellungenStore.getState().schnellzugriffeImportieren(roh.top_sites)

    // Symbole, die kein Lesezeichen mehr braucht, gehen mit dem Import weg.
    const herkuenfte = new Set<string>()
    for (const eintrag of useVerlaufStore.getState().lesezeichen) {
      const h = eintrag.favicon ?? herkunft(eintrag.url)
      if (h) herkuenfte.add(h)
    }
    useBildStore.getState().behalten([...herkuenfte])

    const warnungen = new Set(roh.warnungen.map((w) => w.code))
    if (angepasst.gekuerzt) warnungen.add('speicher')
    if (verlauf.lesezeichenGekuerzt) warnungen.add('lesezeichen_gekuerzt')
    if (verlauf.verlaufGekuerzt) warnungen.add('verlauf_gekuerzt')

    return {
      browser: roh.browser,
      profil: roh.profil,
      dauer: roh.dauer_ms,
      lesezeichen: verlauf.lesezeichen,
      verlauf: verlauf.verlauf,
      topSites,
      favicons,
      warnungen: [...warnungen],
    }
  }

  const importieren = async () => {
    if (!gewaehlt || laeuft) return
    setLaeuft(true)
    setImportfehler(null)
    setErgebnis(null)
    try {
      const roh = await nativ.browserDatenImportieren(gewaehlt)
      if (!roh) throw new Error('nativ')
      setErgebnis(uebernehmen(roh))
      kachelnNeuLaden()
      void nativ
        .kachelSymboleBehalten(useEinstellungenStore.getState().schnellzugriffe.map((eintrag) => eintrag.url))
        .catch(() => undefined)
    } catch (fehler) {
      const code = codeAus(fehler)
      setImportfehler(code)
      // Ein verschwundenes Profil ist mit der alten Liste nicht mehr erreichbar.
      if (code === 'profil_unbekannt') laden()
    } finally {
      setLaeuft(false)
    }
  }

  const optionen = (profile ?? []).map((p) => ({
    value: p.id,
    label: `${browserName(p.browser)} — ${p.name}`,
  }))
  const warnungTexte: Record<string, string> = {
    lesezeichen_gekuerzt: t('browser.einstellungen.import.warnung.lesezeichen_gekuerzt'),
    verlauf_gekuerzt: t('browser.einstellungen.import.warnung.verlauf_gekuerzt'),
    bilder_gekuerzt: t('browser.einstellungen.import.warnung.bilder_gekuerzt'),
    schema: t('browser.einstellungen.import.warnung.schema'),
    lesezeichen_kaputt: t('browser.einstellungen.import.warnung.lesezeichen_kaputt'),
    verlauf_kaputt: t('browser.einstellungen.import.warnung.verlauf_kaputt'),
    bilder_kaputt: t('browser.einstellungen.import.warnung.bilder_kaputt'),
    bilder_fehlen: t('browser.einstellungen.import.warnung.bilder_fehlen'),
    speicher: t('browser.einstellungen.import.warnung.speicher'),
  }
  const fehlerTexte: Record<string, string> = {
    nur_windows: t('browser.einstellungen.import.fehler.nur_windows'),
    profil_unbekannt: t('browser.einstellungen.import.fehler.profil_unbekannt'),
    gesperrt: t('browser.einstellungen.import.fehler.gesperrt'),
    profil_leer: t('browser.einstellungen.import.fehler.profil_leer'),
    zu_gross: t('browser.einstellungen.import.fehler.zu_gross'),
    datei_kaputt: t('browser.einstellungen.import.fehler.datei_kaputt'),
    schatten: t('browser.einstellungen.import.fehler.schatten'),
    abgebrochen: t('browser.einstellungen.import.fehler.abgebrochen'),
  }
  const warnungText = (code: string) =>
    warnungTexte[code] ?? t('browser.einstellungen.import.warnung.generisch')
  const fehlerText = (code: string) =>
    fehlerTexte[code] ?? t('browser.einstellungen.import.fehler.generisch')

  return (
    <Abschnitt titel={t('browser.einstellungen.import.titel')}>
      <p className="-mt-2 text-label-sm text-on-surface-variant">{t('browser.einstellungen.import.hinweis')}</p>

      {profile === null && !ladefehler && (
        <p role="status" className="flex items-center gap-2 text-label-sm text-on-surface-variant">
          <Spinner />
          {t('browser.einstellungen.import.laden')}
        </p>
      )}

      {ladefehler && (
        <div role="alert" className="flex flex-wrap items-center gap-2 text-label-sm text-status-destructive">
          <span>{t('browser.einstellungen.import.ladeFehler')}</span>
          <Button type="button" size="sm" variant="secondary" onClick={laden}>
            {t('browser.einstellungen.import.erneut')}
          </Button>
        </div>
      )}

      {profile !== null && profile.length === 0 && !ladefehler && (
        <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.import.keine')}</p>
      )}

      {profile !== null && profile.length > 0 && (
        <>
          <Auswahlzeile
            name={t('browser.einstellungen.import.profil')}
            wert={gewaehlt}
            optionen={optionen}
            aendern={setGewaehlt}
          />
          <Aktionszeile
            name={t('browser.einstellungen.import.importName')}
            hinweis={t('browser.einstellungen.import.importHinweis')}
          >
            <Button type="button" onClick={() => void importieren()} disabled={!gewaehlt || laeuft}>
              {laeuft && <Spinner />}
              {laeuft ? t('browser.einstellungen.import.laeuft') : t('browser.einstellungen.import.starten')}
            </Button>
          </Aktionszeile>
        </>
      )}

      {importfehler && (
        <p role="alert" className="text-label-sm text-status-destructive">
          {fehlerText(importfehler)}
        </p>
      )}

      {ergebnis && (
        <div
          role="status"
          aria-live="polite"
          className="flex flex-col gap-2 rounded-lg border border-outline-variant bg-surface-container-high p-3"
        >
          <p className="text-body-sm text-on-surface">
            {t('browser.einstellungen.import.ergebnisTitel', {
              browser: browserName(ergebnis.browser),
              profil: ergebnis.profil,
            })}
          </p>
          <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-label-sm">
            <dt className="text-on-surface-variant">{t('browser.einstellungen.import.uebernommenLesezeichen')}</dt>
            <dd className="text-right text-on-surface">{ergebnis.lesezeichen}</dd>
            <dt className="text-on-surface-variant">{t('browser.einstellungen.import.uebernommenVerlauf')}</dt>
            <dd className="text-right text-on-surface">{ergebnis.verlauf}</dd>
            <dt className="text-on-surface-variant">{t('browser.einstellungen.import.uebernommenTopSites')}</dt>
            <dd className="text-right text-on-surface">{ergebnis.topSites}</dd>
            <dt className="text-on-surface-variant">{t('browser.einstellungen.import.uebernommenFavicons')}</dt>
            <dd className="text-right text-on-surface">{ergebnis.favicons}</dd>
            <dt className="text-on-surface-variant">{t('browser.einstellungen.import.dauer')}</dt>
            <dd className="text-right text-on-surface">
              {t('browser.einstellungen.import.sekunden', { wert: (ergebnis.dauer / 1000).toFixed(1) })}
            </dd>
          </dl>
          {ergebnis.lesezeichen + ergebnis.verlauf + ergebnis.topSites + ergebnis.favicons === 0 && (
            <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.import.keineNeu')}</p>
          )}
          {ergebnis.warnungen.length > 0 && (
            <ul className="flex list-disc flex-col gap-1 pl-5 text-label-sm text-status-warning">
              {ergebnis.warnungen.map((code) => (
                <li key={code}>{warnungText(code)}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.import.datenschutz')}</p>
    </Abschnitt>
  )
}
