/**
 * Was die Elemente neben den Stilen zum gewählten Knoten zeigen: berechnete
 * Werte, Box-Modell, Ereignis-Listener, Eigenschaften und Barrierefreiheit.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Checkbox, Input } from '@/Singra/UI'

import { Wert } from './ObjektBaum'
import { ortText, rufen, type Eigenschaft, type Objekt } from './protokoll'
import { useQuellen } from './quellenStore'
import { useAnsicht } from './werkzeuge'

function useLaden<T>(laden: () => Promise<T>, abh: unknown[]): T | null | 'fehler' {
  const [wert, setWert] = useState<T | null | 'fehler'>(null)
  useEffect(() => {
    let gilt = true
    setWert(null)
    laden().then(
      (w) => gilt && setWert(w),
      () => gilt && setWert('fehler'),
    )
    return () => {
      gilt = false
    }
  }, abh)
  return wert
}

function Hinweis({ text }: { text: string }) {
  return <p className="p-3 text-label-sm text-on-surface-variant">{text}</p>
}

interface Gesetzt {
  inlineStyle?: { cssProperties: { name: string }[] }
  matchedCSSRules?: { rule: { style: { cssProperties: { name: string }[] } } }[]
  inherited?: { inlineStyle?: { cssProperties: { name: string }[] }; matchedCSSRules: { rule: { style: { cssProperties: { name: string }[] } } }[] }[]
}

/** Namen, die eine Regel oder das Element selbst setzt, auch geerbt. */
export function gesetzteNamen(m: Gesetzt): Set<string> {
  const aus = new Set<string>()
  const stile = [m.inlineStyle, ...(m.matchedCSSRules ?? []).map((r) => r.rule.style)]
  for (const e of m.inherited ?? []) stile.push(e.inlineStyle, ...e.matchedCSSRules.map((r) => r.rule.style))
  for (const s of stile) for (const p of s?.cssProperties ?? []) aus.add(p.name)
  return aus
}

export function Berechnet({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const [suche, setSuche] = useState('')
  const [alle, setAlle] = useState(false)
  const werte = useLaden(
    () =>
      Promise.all([
        rufen<{ computedStyle: { name: string; value: string }[] }>(tab, 'CSS.getComputedStyleForNode', { nodeId }),
        rufen<Gesetzt>(tab, 'CSS.getMatchedStylesForNode', { nodeId }).catch(() => ({})),
      ]).then(([r, m]) => ({ liste: r.computedStyle ?? [], gesetzt: gesetzteNamen(m) })),
    [tab, nodeId],
  )
  if (werte === null) return <Hinweis text={t('browser.entwickler.laedt')} />
  if (werte === 'fehler') return <Hinweis text={t('browser.entwickler.nichtVerfuegbar')} />
  const s = suche.trim().toLowerCase()
  // Wie in jedem Browser: ohne „Alle zeigen“ nur, was eine Regel gesetzt hat.
  const liste = werte.liste
    .filter((w) => alle || werte.gesetzt.has(w.name))
    .filter((w) => !s || w.name.includes(s) || w.value.toLowerCase().includes(s))
    .sort((a, b) => a.name.localeCompare(b.name))
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-outline-variant/50 p-1.5">
        <div className="min-w-0 flex-1">
          <Input aria-label={t('browser.entwickler.filter')} placeholder={t('browser.entwickler.filter')} value={suche} onChange={(e) => setSuche(e.target.value)} className="h-7 text-label-sm" />
        </div>
        <label className="flex shrink-0 items-center gap-1.5 text-label-sm text-on-surface-variant">
          <Checkbox checked={alle} onCheckedChange={setAlle} />
          {t('browser.entwickler.elemente.alleZeigen')}
        </label>
      </div>
      <dl className="min-h-0 flex-1 select-text overflow-y-auto px-3 py-1 font-mono text-label-sm">
        {liste.map((w) => (
          <div key={w.name} className="flex gap-2 py-px">
            <dt className="shrink-0 text-secondary">{w.name}:</dt>
            <dd className="min-w-0 break-all text-on-surface">{w.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

interface Box {
  content: number[]
  padding: number[]
  border: number[]
  margin: number[]
  width: number
  height: number
}

/** Abstände zwischen zwei Rechtecken (Quads: x1,y1 … x4,y4) oben, rechts, unten, links. */
export function abstaende(aussen: number[], innen: number[]): [number, number, number, number] {
  const r = (n: number) => Math.round(n * 100) / 100
  return [r(innen[1] - aussen[1]), r(aussen[2] - innen[2]), r(aussen[5] - innen[5]), r(innen[0] - aussen[0])]
}

function Ring({ name, werte, farbe, children }: { name: string; werte: [number, number, number, number]; farbe: string; children: React.ReactNode }) {
  const z = (n: number) => (n === 0 ? '–' : n)
  return (
    <div className={`relative border border-dashed border-on-surface-variant/50 px-6 py-5 text-center ${farbe}`}>
      <span className="absolute left-1 top-0.5 text-label-sm text-on-surface-variant">{name}</span>
      <span className="absolute left-1/2 top-0.5 -translate-x-1/2">{z(werte[0])}</span>
      <span className="absolute right-1 top-1/2 -translate-y-1/2">{z(werte[1])}</span>
      <span className="absolute bottom-0.5 left-1/2 -translate-x-1/2">{z(werte[2])}</span>
      <span className="absolute left-1 top-1/2 -translate-y-1/2">{z(werte[3])}</span>
      {children}
    </div>
  )
}

export function Layout({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const box = useLaden(() => rufen<{ model: Box }>(tab, 'DOM.getBoxModel', { nodeId }).then((r) => r.model), [tab, nodeId])
  if (box === null) return <Hinweis text={t('browser.entwickler.laedt')} />
  if (box === 'fehler' || !box) return <Hinweis text={t('browser.entwickler.elemente.ohneBox')} />
  return (
    <div className="flex justify-center overflow-auto p-3 font-mono text-label-sm text-on-surface">
      <Ring name="margin" werte={abstaende(box.margin, box.border)} farbe="bg-[#f6b26b]/25">
        <Ring name="border" werte={abstaende(box.border, box.padding)} farbe="bg-[#ffe599]/25">
          <Ring name="padding" werte={abstaende(box.padding, box.content)} farbe="bg-[#93c47d]/25">
            <div className="bg-[#6fa8dc]/30 px-3 py-2">
              {Math.round(box.width * 100) / 100} × {Math.round(box.height * 100) / 100}
            </div>
          </Ring>
        </Ring>
      </Ring>
    </div>
  )
}

interface Listener {
  type: string
  useCapture: boolean
  passive: boolean
  once: boolean
  scriptId: string
  lineNumber: number
  handler?: Objekt
}

export function Ereignisse({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const setZiel = useAnsicht((s) => s.setZiel)
  const skripte = useQuellen((s) => s.skripte[tab])
  const liste = useLaden(async () => {
    const { object } = await rufen<{ object: Objekt }>(tab, 'DOM.resolveNode', { nodeId, objectGroup: 'elemente' })
    const r = await rufen<{ listeners: Listener[] }>(tab, 'DOMDebugger.getEventListeners', { objectId: object.objectId })
    return r.listeners ?? []
  }, [tab, nodeId])
  if (liste === null) return <Hinweis text={t('browser.entwickler.laedt')} />
  if (liste === 'fehler') return <Hinweis text={t('browser.entwickler.nichtVerfuegbar')} />
  if (liste.length === 0) return <Hinweis text={t('browser.entwickler.elemente.keineListener')} />
  const gruppen = new Map<string, Listener[]>()
  for (const l of liste) gruppen.set(l.type, [...(gruppen.get(l.type) ?? []), l])
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1 text-label-sm">
      {[...gruppen.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([typ, ls]) => (
        <section key={typ} className="border-b border-outline-variant/40 px-3 py-1">
          <h3 className="font-mono font-semibold text-on-surface">{typ}</h3>
          {ls.map((l, i) => {
            const url = skripte?.[l.scriptId]?.url
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 pl-3 font-mono">
                {l.handler && <Wert tab={tab} o={l.handler} />}
                <span className="text-on-surface-variant">
                  {[l.useCapture && 'capture', l.passive && 'passive', l.once && 'once'].filter(Boolean).join(' · ')}
                </span>
                {url && (
                  <button type="button" className="text-on-surface-variant hover:text-primary hover:underline" onClick={() => setZiel({ url, scriptId: l.scriptId, zeile: l.lineNumber })}>
                    {ortText(url, l.lineNumber)}
                  </button>
                )}
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}

/**
 * Alle Eigenschaften des Elements samt Prototypkette, ausgewertet und nach
 * Namen sortiert, wie die Eigenschaften-Ansicht anderer Browser. Methoden fehlen.
 */
const FLACH = `function () {
  const aus = Object.create(null)
  const namen = new Set()
  for (let p = this; p && p !== Object.prototype; p = Object.getPrototypeOf(p)) for (const k of Object.getOwnPropertyNames(p)) namen.add(k)
  for (const k of [...namen].sort()) {
    try {
      const v = this[k]
      if (typeof v !== 'function') aus[k] = v
    } catch {}
  }
  return aus
}`

export function Eigenschaften({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const liste = useLaden(async () => {
    const { object } = await rufen<{ object: Objekt }>(tab, 'DOM.resolveNode', { nodeId, objectGroup: 'elemente' })
    const flach = await rufen<{ result: Objekt }>(tab, 'Runtime.callFunctionOn', { objectId: object.objectId, functionDeclaration: FLACH, silent: true })
    const r = await rufen<{ result?: Eigenschaft[] }>(tab, 'Runtime.getProperties', { objectId: flach.result.objectId, ownProperties: true, generatePreview: true })
    return { object, props: (r.result ?? []).filter((p) => p.value) }
  }, [tab, nodeId])
  if (liste === null) return <Hinweis text={t('browser.entwickler.laedt')} />
  if (liste === 'fehler') return <Hinweis text={t('browser.entwickler.nichtVerfuegbar')} />
  return (
    <div className="min-h-0 flex-1 select-text overflow-y-auto p-3 font-mono text-label-sm">
      <Wert tab={tab} o={liste.object} oben />
      {liste.props.map((p) => (
        <span key={p.name} className="block pl-3">
          <Wert tab={tab} o={p.value!} name={p.name} />
        </span>
      ))}
    </div>
  )
}

interface AxKnoten {
  ignored: boolean
  role?: { value: string }
  name?: { value: string }
  description?: { value: string }
  properties?: { name: string; value: { value: unknown } }[]
}

export function Barrierefreiheit({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const ax = useLaden(
    () => rufen<{ nodes: AxKnoten[] }>(tab, 'Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false }).then((r) => r.nodes?.[0]),
    [tab, nodeId],
  )
  if (ax === null) return <Hinweis text={t('browser.entwickler.laedt')} />
  if (ax === 'fehler' || !ax) return <Hinweis text={t('browser.entwickler.nichtVerfuegbar')} />
  const zeilen: [string, string][] = ax.ignored
    ? [[t('browser.entwickler.elemente.ax.ignoriert'), 'true']]
    : [
        [t('browser.entwickler.elemente.ax.rolle'), ax.role?.value ?? ''],
        [t('browser.entwickler.elemente.ax.name'), ax.name?.value ?? ''],
        ...(ax.description?.value ? [[t('browser.entwickler.elemente.ax.beschreibung'), ax.description.value] as [string, string]] : []),
        ...(ax.properties ?? []).map((p) => [p.name, String(p.value?.value ?? '')] as [string, string]),
      ]
  return (
    <dl className="min-h-0 flex-1 select-text overflow-y-auto px-3 py-2 text-label-sm">
      {zeilen.map(([n, w]) => (
        <div key={n} className="flex gap-2 py-px">
          <dt className="w-28 shrink-0 text-on-surface-variant">{n}</dt>
          <dd className="min-w-0 break-all font-mono text-on-surface">{w || '–'}</dd>
        </div>
      ))}
    </dl>
  )
}
