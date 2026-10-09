/**
 * Die Kästen unter den Schnellzugriffen, in der Reihenfolge aus den
 * Einstellungen (Design). Termine und Notizen gibt es nur gekoppelt und nur,
 * wenn das Modul sichtbar ist; ein privater Tab zeigt nur die Uhr.
 */
import type { ReactNode } from 'react'
import { Calendar, Clock, History, StickyNote, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useSichtbareModule } from '../../leiste/eintraege'
import { WIDGETS, useEinstellungenStore, type Widget } from '../../services/einstellungenStore'
import { seitenHost } from '../../services/geraetKonfig'
import { istGekoppelt, useSitzung } from '../../services/sitzung'
import { seiteOeffnen } from '../../services/tabsStore'
import { useVerlaufStore } from '../../services/verlaufStore'
import { ANZAHL, useJetzt, useNotizen, useTermine, type Termin } from './widgetDaten'

export const WIDGET_SYMBOLE: Record<Widget, LucideIcon> = { uhr: Clock, termine: Calendar, notizen: StickyNote, zuletzt: History }

/** Die Widgets in der Reihenfolge des Nutzers; was die Ordnung nicht nennt, folgt in der Grundordnung. */
export function widgetsGeordnet(ordnung: Widget[]): Widget[] {
  return [...ordnung.filter((w) => WIDGETS.includes(w)), ...WIDGETS.filter((w) => !ordnung.includes(w))]
}

function Karte({ titel, symbol: Symbol, children }: { titel: string; symbol: LucideIcon; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-xl bg-surface-container/80 p-4 backdrop-blur">
      <h2 className="flex items-center gap-2 text-label-md text-on-surface-variant">
        <Symbol className="h-4 w-4" aria-hidden="true" />
        {titel}
      </h2>
      {children}
    </section>
  )
}

function Zeile({ oben, unten, onClick, onAuxClick }: { oben: string; unten: string; onClick: (ctrl: boolean) => void; onAuxClick?: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={(ev) => onClick(ev.ctrlKey || ev.metaKey)}
        onAuxClick={(ev) => ev.button === 1 && onAuxClick?.()}
        className="flex w-full min-w-0 flex-col rounded-md px-2 py-1.5 text-left hover:bg-surface-container-high"
      >
        {/* Ohne max-w-full ragt langer Text unter Android (WebView 124) aus dem Knopf. */}
        <span className="max-w-full truncate text-body-sm text-on-surface">{oben}</span>
        <span className="max-w-full truncate text-label-sm text-on-surface-variant">{unten}</span>
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
        <ul className="flex flex-col">
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
        <ul className="flex flex-col">
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
        <ul className="flex flex-col">
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

export function Widgets({ privat }: { privat: boolean }) {
  const ordnung = useEinstellungenStore((s) => s.widgetOrdnung)
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
    <div className="grid w-full gap-3 sm:grid-cols-2">
      {gezeigt.map((w) =>
        w === 'uhr' ? <Uhr key={w} jetzt={jetzt} /> : w === 'termine' ? <Termine key={w} jetzt={jetzt} /> : w === 'notizen' ? <Notizen key={w} jetzt={jetzt} /> : <Zuletzt key={w} />,
      )}
    </div>
  )
}
