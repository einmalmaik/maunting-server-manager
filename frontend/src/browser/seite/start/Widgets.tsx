/**
 * Die Kästen unter den Schnellzugriffen, in der Reihenfolge aus den
 * Einstellungen (Design). Termine, Notizen und Nachrichten gibt es nur
 * gekoppelt, Termine und Notizen nur, wenn das Modul sichtbar ist; ein
 * privater Tab zeigt nur die Uhr.
 */
import type { ReactNode } from 'react'
import { Calendar, Clock, CloudSun, History, Newspaper, StickyNote, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useSichtbareModule } from '../../leiste/eintraege'
import { Button } from '@/Singra/UI'
import { WIDGETS, useEinstellungenStore, type Widget } from '../../services/einstellungenStore'
import { seitenHost } from '../../services/geraetKonfig'
import { istGekoppelt, useSitzung } from '../../services/sitzung'
import { seiteOeffnen, useTabsStore } from '../../services/tabsStore'
import { useVerlaufStore } from '../../services/verlaufStore'
import { auswahl, useNachrichten } from './nachrichtenDaten'
import { useWetter } from './wetterDaten'
import { ANZAHL, useJetzt, useNotizen, useTermine, type Termin } from './widgetDaten'

export const WIDGET_SYMBOLE: Record<Widget, LucideIcon> = { wetter: CloudSun, uhr: Clock, termine: Calendar, notizen: StickyNote, zuletzt: History, nachrichten: Newspaper }

/** Die Widgets in der Reihenfolge des Nutzers; was die Ordnung nicht nennt, folgt in der Grundordnung. */
export function widgetsGeordnet(ordnung: Widget[]): Widget[] {
  return [...ordnung.filter((w) => WIDGETS.includes(w)), ...WIDGETS.filter((w) => !ordnung.includes(w))]
}

function Karte({ titel, symbol: Symbol, breit, children }: { titel: string; symbol: LucideIcon; breit?: boolean; children: ReactNode }) {
  return (
    <section className={`flex min-w-0 w-full flex-col gap-3 overflow-hidden rounded-xl bg-surface-container/80 p-4 backdrop-blur ${breit ? 'sm:col-span-2' : ''}`}>
      <h2 className="flex min-w-0 items-center gap-2 text-label-md text-on-surface-variant">
        <Symbol className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="truncate">{titel}</span>
      </h2>
      {children}
    </section>
  )
}

function Zeile({ oben, unten, onClick, onAuxClick }: { oben: string; unten: string; onClick: (ctrl: boolean) => void; onAuxClick?: () => void }) {
  return (
    <li className="min-w-0 w-full overflow-hidden">
      <button
        type="button"
        onClick={(ev) => onClick(ev.ctrlKey || ev.metaKey)}
        onAuxClick={(ev) => ev.button === 1 && onAuxClick?.()}
        className="flex w-full min-w-0 flex-col overflow-hidden rounded-md px-2 py-1.5 text-left hover:bg-surface-container-high"
      >
        {/* max-w-full und truncate schneiden lange Titel und Adressen strikt innerhalb der Box ab. */}
        <span className="block max-w-full min-w-0 truncate text-body-sm text-on-surface">{oben}</span>
        <span className="block max-w-full min-w-0 truncate text-label-sm text-on-surface-variant">{unten}</span>
      </button>
    </li>
  )
}

function Leer({ text }: { text: string }) {
  return <p className="px-2 text-body-sm text-on-surface-variant">{text}</p>
}

/** Ist kein Titel lesbar (dem Gerät fehlt der Schlüssel), eine Zeile statt vieler gleicher; die Seite sagt, was fehlt. */
function Unlesbar({ ziel }: { ziel: 'kalender' | 'notizen' }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <ul className="flex flex-col">
      <Zeile oben={t('browser.start.nichtLesbarAlle')} unten={t(`browser.start.oeffnen.${ziel}`)} onClick={() => navigate(`/${ziel}`)} />
    </ul>
  )
}

function Uhr({ jetzt }: { jetzt: number }) {
  const { t, i18n } = useTranslation()
  const d = new Date(jetzt)
  return (
    <Karte titel={t('browser.start.widget.uhr')} symbol={Clock}>
      <p className="px-2 font-headline text-display-sm tabular-nums text-on-surface">
        {d.toLocaleTimeString(i18n.language, { hour: '2-digit', minute: '2-digit' })}
      </p>
      <p className="px-2 text-body-sm text-on-surface-variant">{d.toLocaleDateString(i18n.language, { weekday: 'long', day: 'numeric', month: 'long' })}</p>
    </Karte>
  )
}

function terminZeit(termin: Termin, jetzt: number, sprache: string, t: (k: string) => string): string {
  const heute = new Date(jetzt).toDateString()
  const morgen = new Date(jetzt + 24 * 3600_000).toDateString()
  const tag = termin.start.toDateString()
  const tagText =
    tag === heute ? t('browser.start.heute') : tag === morgen ? t('browser.start.morgen') : termin.start.toLocaleDateString(sprache, { weekday: 'short', day: 'numeric', month: 'numeric' })
  if (termin.ganztags) return `${tagText}, ${t('browser.start.ganztags')}`
  return `${tagText}, ${termin.start.toLocaleTimeString(sprache, { hour: '2-digit', minute: '2-digit' })}`
}

function Termine({ jetzt }: { jetzt: number }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const termine = useTermine(jetzt)
  return (
    <Karte titel={t('browser.start.widget.termine')} symbol={Calendar}>
      {termine && termine.length === 0 && <Leer text={t('browser.start.keineTermine')} />}
      {termine && termine.length > 0 && termine.every((e) => !e.titel) && <Unlesbar ziel="kalender" />}
      {termine && termine.some((e) => e.titel) && (
        <ul className="flex min-w-0 w-full flex-col">
          {termine.map((e) => (
            <Zeile key={e.schluessel} oben={e.titel ?? t('browser.start.nichtLesbar')} unten={terminZeit(e, jetzt, i18n.language, t)} onClick={() => navigate('/kalender')} />
          ))}
        </ul>
      )}
    </Karte>
  )
}

function Notizen({ jetzt }: { jetzt: number }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const notizen = useNotizen(jetzt)
  return (
    <Karte titel={t('browser.start.widget.notizen')} symbol={StickyNote}>
      {notizen && notizen.length === 0 && <Leer text={t('browser.start.keineNotizen')} />}
      {notizen && notizen.length > 0 && notizen.every((n) => !n.titel) && <Unlesbar ziel="notizen" />}
      {notizen && notizen.some((n) => n.titel) && (
        <ul className="flex min-w-0 w-full flex-col">
          {notizen.map((n) => (
            <Zeile
              key={n.schluessel}
              oben={n.titel ?? t('browser.start.nichtLesbar')}
              unten={n.geaendert.toLocaleDateString(i18n.language, { day: 'numeric', month: 'short' })}
              onClick={() => navigate('/notizen')}
            />
          ))}
        </ul>
      )}
    </Karte>
  )
}

function Zuletzt() {
  const { t } = useTranslation()
  const verlauf = useVerlaufStore((s) => s.verlauf)
  const gezeigt: typeof verlauf = []
  for (const e of verlauf) {
    if (gezeigt.length >= ANZAHL) break
    if (!gezeigt.some((g) => g.url === e.url)) gezeigt.push(e)
  }
  return (
    <Karte titel={t('browser.start.widget.zuletzt')} symbol={History}>
      {gezeigt.length === 0 && <Leer text={t('browser.verlauf.leer')} />}
      {gezeigt.length > 0 && (
        <ul className="flex min-w-0 w-full flex-col">
          {gezeigt.map((e) => (
            <Zeile
              key={`${e.url}-${e.zeit}`}
              oben={e.titel || e.url}
              unten={seitenHost(e.url) ?? e.url}
              onClick={(ctrl) => seiteOeffnen(e.url, ctrl)}
              onAuxClick={() => seiteOeffnen(e.url, true)}
            />
          ))}
        </ul>
      )}
    </Karte>
  )
}

/** „vor 3 Stunden“ in der App-Sprache; ohne Zeit nichts. */
export function alter(zeit: string | null, jetzt: number, sprache: string): string | null {
  const t = zeit ? Date.parse(zeit) : NaN
  if (Number.isNaN(t)) return null
  const minuten = Math.max(0, Math.round((jetzt - t) / 60_000))
  const fmt = new Intl.RelativeTimeFormat(sprache, { numeric: 'auto' })
  if (minuten < 60) return fmt.format(-minuten, 'minute')
  if (minuten < 24 * 60) return fmt.format(-Math.round(minuten / 60), 'hour')
  return fmt.format(-Math.round(minuten / (24 * 60)), 'day')
}

function Nachrichten({ jetzt }: { jetzt: number }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const themen = useEinstellungenStore((s) => s.nachrichtenThemen)
  const worte = useEinstellungenStore((s) => s.nachrichtenWorte)
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const { stand, fehler } = useNachrichten()
  const meldungen = stand ? auswahl(stand, themen, worte) : null
  return (
    <Karte titel={t('browser.start.widget.nachrichten')} symbol={Newspaper} breit>
      {!gekoppelt && !stand && (
        <div className="flex flex-col items-start gap-2 px-2">
          <p className="text-body-sm text-on-surface-variant">{t('browser.start.nachrichten.ungekoppelt')}</p>
          <Button type="button" size="sm" variant="secondary" onClick={() => navigate('/koppeln')}>
            {t('browser.start.nachrichten.koppeln')}
          </Button>
        </div>
      )}
      {gekoppelt && !stand && fehler && (
        <Leer text={t(fehler === 'nichtEingerichtet' ? 'browser.start.nachrichten.nichtEingerichtet' : 'browser.start.nachrichten.fehler')} />
      )}
      {stand && meldungen?.length === 0 && (
        <Leer text={t(worte.trim() ? 'browser.start.nachrichten.keineZuWorten' : 'browser.start.nachrichten.keine')} />
      )}
      {meldungen && meldungen.length > 0 && (
        <ul className="grid min-w-0 w-full gap-1 sm:grid-cols-2 sm:gap-x-3">
          {meldungen.map((m) => {
            const host = seitenHost(m.url) ?? m.url
            const wann = alter(m.zeit, jetzt, i18n.language)
            return (
              <Zeile
                key={m.url}
                oben={m.titel || host}
                unten={wann ? `${host} · ${wann}` : host}
                onClick={(ctrl) => seiteOeffnen(m.url, ctrl)}
                onAuxClick={() => seiteOeffnen(m.url, true)}
              />
            )
          })}
        </ul>
      )}
    </Karte>
  )
}

function Wetter() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const einstellungen = useTabsStore((s) => s.einstellungen)
  const ort = useEinstellungenStore((s) => s.wetterOrt)
  const einheit = useEinstellungenStore((s) => s.wetterEinheit)
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const { stand, fehler } = useWetter()
  const grad = (celsius: number) =>
    einheit === 'fahrenheit' ? `${Math.round(celsius * 1.8 + 32)}°F` : `${Math.round(celsius)}°C`
  return (
    <Karte titel={t('browser.start.widget.wetter')} symbol={CloudSun} breit>
      {!gekoppelt && !stand && (
        <div className="flex flex-col items-start gap-2 px-2">
          <p className="text-body-sm text-on-surface-variant">{t('browser.start.wetter.ungekoppelt')}</p>
          <Button type="button" size="sm" variant="secondary" onClick={() => navigate('/koppeln')}>
            {t('browser.start.wetter.koppeln')}
          </Button>
        </div>
      )}
      {gekoppelt && !ort && (
        <div className="flex flex-col items-start gap-2 px-2">
          <p className="text-body-sm text-on-surface-variant">{t('browser.start.wetter.nichtKonfiguriert')}</p>
          <Button type="button" size="sm" variant="secondary" onClick={() => einstellungen('design')}>
            {t('browser.start.wetter.ortWaehlen')}
          </Button>
        </div>
      )}
      {gekoppelt && ort && !stand && (
        <Leer text={t(fehler === 'nichtEingerichtet' ? 'browser.start.wetter.nichtEingerichtet' : 'browser.start.wetter.fehler')} />
      )}
      {stand && (
        <>
          <div className="flex items-end gap-3 px-2">
            <p className="font-headline text-display-sm tabular-nums text-on-surface">{grad(stand.jetzt.temp)}</p>
            <div className="pb-1">
              <p className="text-body-sm text-on-surface">{t(`browser.start.wetter.lage.${stand.jetzt.lage}`)}</p>
              <p className="text-label-sm text-on-surface-variant">{ort?.name ?? ''}</p>
            </div>
          </div>
          <ul className="flex min-w-0 gap-2 px-2">
            {stand.stunden.slice(0, 3).map((stunde) => (
              <li
                key={stunde.zeit}
                className="flex min-w-0 flex-1 flex-col items-center gap-1 rounded-lg bg-surface-container-high/50 py-2 text-label-sm text-on-surface-variant"
              >
                <span>{new Date(stunde.zeit).toLocaleTimeString(i18n.language, { hour: '2-digit' })}</span>
                <CloudSun className="h-4 w-4" aria-hidden="true" />
                <span className="tabular-nums text-on-surface">{grad(stunde.temp)}</span>
              </li>
            ))}
          </ul>
          {stand.warnungen && stand.warnungen.length > 0 && (
            <div className="mx-2 rounded-lg border border-status-warning/30 bg-status-warning/10 p-2">
              <p className="text-label-sm font-semibold text-status-warning">{stand.warnungen[0].titel}</p>
              <p className="mt-1 text-label-sm leading-relaxed text-on-surface-variant">{stand.warnungen[0].text}</p>
              <p className="mt-1 text-label-sm text-on-surface-variant/70">
                {t('browser.start.wetter.warnquelle')}
              </p>
            </div>
          )}
          <p className="px-2 text-label-sm text-on-surface-variant">{t('browser.start.wetter.quelle')}</p>
        </>
      )}
    </Karte>
  )
}

export function Widgets({ privat }: { privat: boolean }) {  const ordnung = useEinstellungenStore((s) => s.widgetOrdnung)
  const aus = useEinstellungenStore((s) => s.widgetsAus)
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const module = useSichtbareModule()
  const jetzt = useJetzt()

  const da = (w: Widget) => {
    if (aus.includes(w)) return false
    if (privat) return w === 'uhr'
    if (w === 'termine') return gekoppelt && module.has('kalender')
    if (w === 'notizen') return gekoppelt && module.has('notizen')
    return true
  }
  const gezeigt = widgetsGeordnet(ordnung).filter(da)
  if (gezeigt.length === 0) return null

  return (
    <div className="grid min-w-0 w-full gap-3 sm:grid-cols-2">
      {gezeigt.map((w) =>
        w === 'wetter' ? (
          <Wetter key={w} />
        ) : w === 'uhr' ? (
          <Uhr key={w} jetzt={jetzt} />
        ) : w === 'termine' ? (
          <Termine key={w} jetzt={jetzt} />
        ) : w === 'notizen' ? (
          <Notizen key={w} jetzt={jetzt} />
        ) : w === 'nachrichten' ? (
          <Nachrichten key={w} jetzt={jetzt} />
        ) : (
          <Zuletzt key={w} />
        ),
      )}
    </div>
  )
}
