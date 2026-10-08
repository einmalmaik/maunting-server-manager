/**
 * Die Quellen: alle Dateien der Seite (Dokumente, Skripte, Stile, Bilder),
 * ihr Inhalt, Haltepunkte und der Debugger. Der Debugger läuft erst, wenn die
 * Quellen das erste Mal offen sind (`debuggerAn`).
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, RotateCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Input, prompt } from '@/Singra/UI'

import { CodeAnsicht } from './CodeAnsicht'
import { DebuggerLeiste, DebuggerSeite } from './Debugger'
import { Knopf, Leiste } from './Leiste'
import { rufen } from './protokoll'
import { useQuellen } from './quellenStore'
import { debuggerAn, useAnsicht } from './werkzeuge'

interface Datei {
  url: string
  typ: string
  mime: string
  frameId?: string
  scriptId?: string
}

interface Rahmenbaum {
  frame: { id: string; url: string; mimeType?: string }
  resources: { url: string; type: string; mimeType: string }[]
  childFrames?: Rahmenbaum[]
}

/** Alle Dateien aus dem Baum der Rahmen. */
export function dateienAus(baum: Rahmenbaum): Datei[] {
  const aus: Datei[] = [{ url: baum.frame.url, typ: 'Document', mime: baum.frame.mimeType ?? 'text/html', frameId: baum.frame.id }]
  for (const r of baum.resources) aus.push({ url: r.url, typ: r.type, mime: r.mimeType, frameId: baum.frame.id })
  for (const k of baum.childFrames ?? []) aus.push(...dateienAus(k))
  return aus
}

function anzeige(url: string): { name: string; ort: string } {
  try {
    const u = new URL(url)
    const teile = u.pathname.split('/')
    const name = teile.pop() || '(index)'
    const ort = teile.filter(Boolean).join('/')
    return { name: name + u.search, ort: u.host + (ort ? `/${ort}` : '') }
  } catch {
    return { name: url, ort: '' }
  }
}

type Inhalt = { art: 'text'; zeilen: string[] } | { art: 'bild'; src: string } | { art: 'fehler' }

export function Quellen({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const skripte = useQuellen((s) => s.skripte[tab])
  const haltepunkte = useQuellen((s) => s.haltepunkte[tab])
  const halt = useQuellen((s) => s.halt[tab])
  const ziel = useAnsicht((s) => s.ziel)
  const [baum, setBaum] = useState<Datei[]>([])
  const [filter, setFilter] = useState('')
  const [liste, setListe] = useState(true)
  const [offen, setOffen] = useState<Datei | null>(null)
  const [inhalt, setInhalt] = useState<Inhalt | null>(null)
  const [sprung, setSprung] = useState<number | null>(null)
  const [suche, setSuche] = useState('')

  const neuLaden = () =>
    void rufen<{ frameTree: Rahmenbaum }>(tab, 'Page.getResourceTree')
      .then((r) => setBaum(r.frameTree ? dateienAus(r.frameTree) : []))
      .catch(() => setBaum([]))

  useEffect(() => {
    void debuggerAn(tab)
    neuLaden()
  }, [tab])

  // Skripte ohne Eintrag im Rahmenbaum (nachgeladen) kommen dazu.
  const dateien = useMemo(() => {
    const nachUrl = new Map<string, Datei>()
    for (const d of baum) nachUrl.set(d.url, d)
    for (const s of Object.values(skripte ?? {})) {
      const da = nachUrl.get(s.url)
      nachUrl.set(s.url, da ? { ...da, scriptId: da.scriptId ?? s.scriptId } : { url: s.url, typ: 'Script', mime: 'text/javascript', scriptId: s.scriptId })
    }
    const f = filter.trim().toLowerCase()
    return [...nachUrl.values()].filter((d) => !f || d.url.toLowerCase().includes(f)).sort((a, b) => a.url.localeCompare(b.url))
  }, [baum, skripte, filter])

  // Ein Sprung von außen (Konsole, Netzwerk, Aufrufstapel) oder der Halt öffnet die Stelle.
  useEffect(() => {
    const stelle = ziel ?? (halt ? { url: halt.rahmen[halt.gewaehlt]?.url ?? '', scriptId: halt.rahmen[halt.gewaehlt]?.location.scriptId, zeile: halt.rahmen[halt.gewaehlt]?.location.lineNumber ?? 0 } : null)
    if (!stelle) return
    const d = dateien.find((x) => (stelle.scriptId && x.scriptId === stelle.scriptId) || x.url === stelle.url) ?? (stelle.scriptId ? { url: stelle.url, typ: 'Script', mime: 'text/javascript', scriptId: stelle.scriptId } : null)
    if (ziel) useAnsicht.getState().setZiel(null)
    if (!d) return
    if (d.url !== offen?.url) setOffen(d)
    setSprung(stelle.zeile)
    setListe(false)
  }, [ziel, halt])

  useEffect(() => {
    if (!offen) return
    let gilt = true
    setInhalt(null)
    const laden = offen.scriptId
      ? rufen<{ scriptSource: string }>(tab, 'Debugger.getScriptSource', { scriptId: offen.scriptId }).then((r) => ({ content: r.scriptSource, base64Encoded: false }))
      : rufen<{ content: string; base64Encoded: boolean }>(tab, 'Page.getResourceContent', { frameId: offen.frameId, url: offen.url })
    laden.then(
      (r) => {
        if (!gilt) return
        if (r.base64Encoded && offen.mime.startsWith('image/')) setInhalt({ art: 'bild', src: `data:${offen.mime};base64,${r.content}` })
        else setInhalt({ art: 'text', zeilen: (r.base64Encoded ? new TextDecoder().decode(Uint8Array.from(atob(r.content), (c) => c.charCodeAt(0))) : (r.content ?? '')).split(/\r?\n/) })
      },
      () => gilt && setInhalt({ art: 'fehler' }),
    )
    return () => {
      gilt = false
    }
  }, [tab, offen])

  const haltMarken = useMemo(() => {
    const m = new Map<number, string>()
    for (const h of haltepunkte ?? []) if (h.url === offen?.url) m.set(h.zeile, h.bedingung)
    return m
  }, [haltepunkte, offen])

  const haltepunktWeg = (id: string) => {
    void rufen(tab, 'Debugger.removeBreakpoint', { breakpointId: id }).catch(() => null)
    useQuellen.getState().haltepunktWeg(tab, id)
  }

  const nummer = async (zeile: number, mitBedingung: boolean) => {
    if (!offen) return
    const da = (haltepunkte ?? []).find((h) => h.url === offen.url && h.zeile === zeile)
    if (da && !mitBedingung) return haltepunktWeg(da.id)
    let bedingung = da?.bedingung ?? ''
    if (mitBedingung) {
      const antwort = await prompt({ message: t('browser.entwickler.quellen.bedingungText', { zeile: zeile + 1 }), defaultValue: bedingung, placeholder: 'x > 3' })
      if (antwort === null) return
      bedingung = antwort
    }
    if (da) haltepunktWeg(da.id)
    const r = await rufen<{ breakpointId: string }>(tab, 'Debugger.setBreakpointByUrl', { url: offen.url, lineNumber: zeile, condition: bedingung }).catch(() => null)
    if (r?.breakpointId) useQuellen.getState().haltepunkt(tab, { id: r.breakpointId, url: offen.url, zeile, bedingung })
  }

  const suchen = (richtung: 1 | -1) => {
    if (inhalt?.art !== 'text' || !suche) return
    const z = inhalt.zeilen
    const start = sprung ?? -1
    for (let n = 1; n <= z.length; n++) {
      const i = (((start + richtung * n) % z.length) + z.length) % z.length
      if (z[i].toLowerCase().includes(suche.toLowerCase())) return setSprung(i)
    }
  }

  const aktuell = halt && offen && halt.rahmen[halt.gewaehlt]?.location.scriptId === offen.scriptId ? halt.rahmen[halt.gewaehlt].location.lineNumber : null
  const istSkript = !!offen && (offen.typ === 'Script' || !!offen.scriptId)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <DebuggerLeiste tab={tab} />
      <div className="flex shrink-0 items-center gap-1 border-b border-outline-variant px-2 py-1">
        <button type="button" aria-expanded={liste} onClick={() => setListe(!liste)} className="flex min-w-0 flex-1 items-center gap-1 text-left text-label-sm">
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${liste ? '' : '-rotate-90'}`} aria-hidden="true" />
          <span className="truncate font-mono">{offen ? anzeige(offen.url).name : t('browser.entwickler.quellen.dateien', { zahl: dateien.length })}</span>
        </button>
        <Knopf text={t('browser.entwickler.quellen.neuLaden')} onClick={neuLaden}>
          <RotateCw className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      </div>
      {liste && (
        <div className="flex max-h-[40%] min-h-[6rem] shrink-0 flex-col border-b border-outline-variant">
          <div className="p-1.5">
            <Input aria-label={t('browser.entwickler.quellen.dateiSuchen')} placeholder={t('browser.entwickler.quellen.dateiSuchen')} value={filter} onChange={(e) => setFilter(e.target.value)} className="h-7 text-label-sm" />
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto text-label-sm" aria-label={t('browser.entwickler.quellen.dateienListe')}>
            {dateien.map((d) => {
              const a = anzeige(d.url)
              return (
                <li key={d.url}>
                  <button
                    type="button"
                    aria-current={offen?.url === d.url}
                    onClick={() => {
                      setOffen(d)
                      setSprung(null)
                      setListe(false)
                    }}
                    className={`flex w-full gap-2 px-3 py-0.5 text-left ${offen?.url === d.url ? 'bg-primary/15' : 'hover:bg-surface-container-high'}`}
                  >
                    <span className="truncate font-mono">{a.name}</span>
                    <span className="min-w-0 flex-1 truncate text-on-surface-variant">{a.ort}</span>
                    <span className="shrink-0 text-on-surface-variant">{d.typ}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
      {offen && inhalt?.art === 'text' && (
        <Leiste>
          <div className="min-w-0 flex-1">
            <Input
              aria-label={t('browser.entwickler.quellen.imCodeSuchen')}
              placeholder={t('browser.entwickler.quellen.imCodeSuchen')}
              value={suche}
              onChange={(e) => setSuche(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && suchen(e.shiftKey ? -1 : 1)}
              className="h-7 text-label-sm"
            />
          </div>
          <Knopf text={t('browser.entwickler.zurueck')} onClick={() => suchen(-1)} disabled={!suche}>
            <ChevronUp className="h-4 w-4" aria-hidden="true" />
          </Knopf>
          <Knopf text={t('browser.entwickler.weiter')} onClick={() => suchen(1)} disabled={!suche}>
            <ChevronDown className="h-4 w-4" aria-hidden="true" />
          </Knopf>
        </Leiste>
      )}
      <div className="flex min-h-[8rem] flex-[3] flex-col">
        {!offen ? (
          <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.quellen.waehlen')}</p>
        ) : !inhalt ? (
          <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.laedt')}</p>
        ) : inhalt.art === 'fehler' ? (
          <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.quellen.nichtLesbar')}</p>
        ) : inhalt.art === 'bild' ? (
          <div className="flex justify-center overflow-auto p-3">
            <img src={inhalt.src} alt={offen.url} className="max-w-full" />
          </div>
        ) : (
          <CodeAnsicht zeilen={inhalt.zeilen} aktuell={aktuell} ziel={sprung} haltepunkte={haltMarken} onNummer={istSkript ? (z, b) => void nummer(z, b) : undefined} />
        )}
      </div>
      <DebuggerSeite tab={tab} haltepunktWeg={haltepunktWeg} />
    </div>
  )
}
