/**
 * Die Konsole: Meldungen der Seite mit Stufe, Herkunft und Stapel, Gruppen,
 * Filter nach Stufe und Text, und die Eingabe.
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, CircleAlert, CircleX, Info, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Checkbox, Input } from '@/Singra/UI'

import { KonsolenEingabe } from './KonsolenEingabe'
import { useKonsole, type Eintrag, type Stufe } from './konsoleStore'
import { FARBE, Wert, wertText } from './ObjektBaum'
import { ortText, rufen, type Stapel } from './protokoll'
import { Knopf, Leiste } from './Leiste'
import { useAnsicht } from './werkzeuge'

const LEER: Eintrag[] = []

const ZEILE: Record<Stufe, string> = {
  log: '',
  info: '',
  debug: 'text-on-surface-variant',
  warn: 'bg-status-warning/10 text-status-warning',
  error: 'bg-status-destructive/10 text-status-destructive',
}

type Filter = 'error' | 'warn' | 'info' | 'debug'
const FILTER: Filter[] = ['error', 'warn', 'info', 'debug']
/** `log` zählt wie Chrome zu „Info“. */
const filterVon = (s: Stufe): Filter => (s === 'log' ? 'info' : s)

function Ort({ url, zeile }: { url: string; zeile: number }) {
  const setZiel = useAnsicht((s) => s.setZiel)
  return (
    <button type="button" onClick={() => setZiel({ url, zeile })} className="shrink-0 text-on-surface-variant underline-offset-2 hover:text-primary hover:underline">
      {ortText(url, zeile)}
    </button>
  )
}

function StapelAnsicht({ stapel }: { stapel: Stapel }) {
  const rahmen: { name: string; url: string; zeile: number }[] = []
  for (let s: Stapel | undefined = stapel; s; s = s.parent) {
    for (const r of s.callFrames) rahmen.push({ name: r.functionName || '(anonym)', url: r.url, zeile: r.lineNumber })
  }
  return (
    <span className="block pl-4 text-on-surface-variant">
      {rahmen.map((r, i) => (
        <span key={i} className="flex gap-2">
          <span className="truncate">at {r.name}</span>
          {r.url && <Ort url={r.url} zeile={r.zeile} />}
        </span>
      ))}
    </span>
  )
}

function Zeile({ tab, e, zu, umschalten }: { tab: string; e: Eintrag; zu: boolean; umschalten: () => void }) {
  const { t } = useTranslation()
  const [stapelOffen, setStapelOffen] = useState(false)
  const symbol =
    e.stufe === 'error' ? <CircleX className="h-3.5 w-3.5" aria-hidden="true" /> : e.stufe === 'warn' ? <CircleAlert className="h-3.5 w-3.5" aria-hidden="true" /> : null
  let inhalt: React.ReactNode
  if (e.art === 'geleert') inhalt = <span className="italic text-on-surface-variant">{t('browser.entwickler.konsole.geleert')}</span>
  else if (e.art === 'navigiert') inhalt = <span className="text-primary">{t('browser.entwickler.konsole.navigiert', { url: e.text })}</span>
  else if (e.art === 'eingabe') inhalt = <span className="whitespace-pre-wrap text-primary">{e.text}</span>
  else
    inhalt = (
      <>
        {e.text !== undefined && <span className="whitespace-pre-wrap">{e.text}</span>}
        {e.werte.map((w, i) => (
          <span key={i} className="mr-2 whitespace-pre-wrap">
            <Wert tab={tab} o={w} oben={e.art === 'meldung'} />
          </span>
        ))}
      </>
    )
  return (
    <div className={`flex items-start gap-1.5 border-b border-outline-variant/40 py-0.5 pr-2 ${ZEILE[e.stufe]}`} style={{ paddingLeft: 8 + e.ebene * 14 }}>
      <span className="mt-0.5 w-3.5 shrink-0">
        {e.gruppe ? (
          <button type="button" onClick={umschalten} aria-expanded={!zu} aria-label={t('browser.entwickler.konsole.gruppe')}>
            <ChevronRight className={`h-3.5 w-3.5 transition-transform ${zu ? '' : 'rotate-90'}`} aria-hidden="true" />
          </button>
        ) : e.art === 'eingabe' ? (
          <span className="text-primary">›</span>
        ) : e.art === 'ergebnis' ? (
          <span className={FARBE.leer}>←</span>
        ) : (
          symbol
        )}
      </span>
      {e.anzahl > 1 && <span className="mt-0.5 shrink-0 rounded-full bg-surface-container-highest px-1.5 text-label-sm tabular-nums text-on-surface">{e.anzahl}</span>}
      <span className={`min-w-0 flex-1 break-words ${e.gruppe ? 'font-semibold' : ''}`}>
        {inhalt}
        {e.stapel && e.stapel.callFrames.length > 0 && (
          <button type="button" onClick={() => setStapelOffen(!stapelOffen)} className="ml-1 text-on-surface-variant hover:text-primary" aria-expanded={stapelOffen}>
            {stapelOffen ? t('browser.entwickler.konsole.stapelZu') : t('browser.entwickler.konsole.stapel')}
          </button>
        )}
        {stapelOffen && e.stapel && <StapelAnsicht stapel={e.stapel} />}
      </span>
      {e.quelle && <Ort url={e.quelle.url} zeile={e.quelle.zeile} />}
    </div>
  )
}

/** Textform einer Zeile, für den Textfilter. */
function suchtext(e: Eintrag): string {
  return [e.text ?? '', ...e.werte.map((w) => wertText(w, true)), e.quelle?.url ?? ''].join(' ').toLowerCase()
}

export function Konsole({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const eintraege = useKonsole((s) => s.eintraege[tab] ?? LEER)
  const beibehalten = useKonsole((s) => s.beibehalten)
  const [stufen, setStufen] = useState<Record<Filter, boolean>>({ error: true, warn: true, info: true, debug: false })
  const [suche, setSuche] = useState('')
  const [umgeschaltet, setUmgeschaltet] = useState<Record<number, boolean>>({})
  const liste = useRef<HTMLDivElement>(null)
  const unten = useRef(true)

  const zahl = useMemo(() => {
    const z: Record<Filter, number> = { error: 0, warn: 0, info: 0, debug: 0 }
    for (const e of eintraege) if (e.art === 'meldung') z[filterVon(e.stufe)] += e.anzahl
    return z
  }, [eintraege])

  const sichtbar = useMemo(() => {
    const s = suche.trim().toLowerCase()
    const aus: { e: Eintrag; zu: boolean }[] = []
    let versteckt: number | null = null
    for (const e of eintraege) {
      if (versteckt !== null && e.ebene > versteckt) continue
      versteckt = null
      const zu = e.gruppe ? (umgeschaltet[e.nr] ?? e.gruppe === 'zu') : false
      if (zu) versteckt = e.ebene
      if (e.art === 'meldung' && !stufen[filterVon(e.stufe)]) continue
      if (s && !suchtext(e).includes(s)) continue
      aus.push({ e, zu })
    }
    return aus
  }, [eintraege, stufen, suche, umgeschaltet])

  // Folgt dem Ende, solange man nicht hochgerollt hat.
  useLayoutEffect(() => {
    const el = liste.current
    if (el && unten.current) el.scrollTop = el.scrollHeight
  }, [sichtbar])

  const leeren = () => {
    useKonsole.getState().leeren(tab)
    void rufen(tab, 'Runtime.releaseObjectGroup', { objectGroup: 'konsole' }).catch(() => null)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Leiste>
        <Knopf text={t('browser.entwickler.konsole.leeren')} onClick={leeren}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Knopf>
        <div className="w-40 min-w-32 flex-1">
          <Input
            aria-label={t('browser.entwickler.filter')}
            placeholder={t('browser.entwickler.filter')}
            value={suche}
            onChange={(e) => setSuche(e.target.value)}
            className="h-7 text-label-sm"
          />
        </div>
        <div role="group" aria-label={t('browser.entwickler.konsole.stufen')} className="flex flex-wrap gap-1">
          {FILTER.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={stufen[f]}
              onClick={() => setStufen({ ...stufen, [f]: !stufen[f] })}
              className={`flex h-7 items-center gap-1 rounded-md px-2 text-label-sm [@media(pointer:coarse)]:h-11 ${
                stufen[f] ? 'bg-primary/15 text-on-surface' : 'text-on-surface-variant hover:bg-surface-container-high'
              }`}
            >
              {f === 'error' && <CircleX className="h-3 w-3 text-status-destructive" aria-hidden="true" />}
              {f === 'warn' && <CircleAlert className="h-3 w-3 text-status-warning" aria-hidden="true" />}
              {f === 'info' && <Info className="h-3 w-3" aria-hidden="true" />}
              {t(`browser.entwickler.konsole.stufe.${f}`)}
              {zahl[f] > 0 && (
                <>
                  <span className="sr-only">, </span>
                  <span className="tabular-nums text-on-surface-variant">{zahl[f]}</span>
                </>
              )}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
          <Checkbox checked={beibehalten} onCheckedChange={(an) => useKonsole.getState().setBeibehalten(an)} />
          {t('browser.entwickler.beibehalten')}
        </label>
      </Leiste>
      <div
        ref={liste}
        role="log"
        aria-label={t('browser.entwickler.konsole.titel')}
        onScroll={(e) => {
          const el = e.currentTarget
          unten.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
        }}
        className="min-h-0 flex-1 select-text overflow-y-auto font-mono text-label-sm"
      >
        {sichtbar.length === 0 && <p className="p-3 font-sans text-on-surface-variant">{t('browser.entwickler.konsole.leer')}</p>}
        {sichtbar.map(({ e, zu }) => (
          <Zeile key={e.nr} tab={tab} e={e} zu={zu} umschalten={() => setUmgeschaltet({ ...umgeschaltet, [e.nr]: !zu })} />
        ))}
      </div>
      <KonsolenEingabe tab={tab} />
    </div>
  )
}
