/**
 * Ein Wert aus der Seite (`Runtime.RemoteObject`), so wie Konsole, Bereiche
 * im Debugger und IndexedDB ihn zeigen: einfache Werte direkt, Objekte mit
 * Vorschau und zum Aufklappen. Aufgeklappt wird erst auf Klick
 * (`Runtime.getProperties`).
 */
import { useEffect, useState } from 'react'
import { ChevronRight, Crosshair } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

import { hervorheben, rufen, type Eigenschaft, type Objekt, type ObjektVorschau } from './protokoll'
import { aufdecken } from './werkzeuge'

export const FARBE = {
  string: 'text-status-success',
  zahl: 'text-primary',
  leer: 'text-on-surface-variant',
  name: 'text-secondary',
  fehler: 'text-status-destructive',
}

/** Kurzform eines Wertes in einer Vorschau. */
function kurz(typ: string, subtype: string | undefined, wert: string | undefined): string {
  if (typ === 'string') return JSON.stringify(wert ?? '')
  if (typ === 'function') return 'ƒ'
  if (typ === 'object' && subtype !== 'null' && !wert) return '{…}'
  return wert ?? typ
}

export function vorschauText(p: ObjektVorschau): string {
  if (p.subtype === 'array' || p.subtype === 'typedarray') {
    const teile = p.properties.map((e) => (/^\d+$/.test(e.name) ? '' : `${e.name}: `) + kurz(e.type, e.subtype, e.value))
    return `${p.description ?? ''} [${teile.join(', ')}${p.overflow ? ', …' : ''}]`
  }
  if (p.entries) {
    const teile = p.entries.map((e) => (e.key ? `${e.key.description ?? ''} => ` : '') + (e.value.description ?? ''))
    return `${p.description ?? ''} {${teile.join(', ')}${p.overflow ? ', …' : ''}}`
  }
  const teile = p.properties.map((e) => `${e.name}: ${kurz(e.type, e.subtype, e.value)}`)
  const name = p.description && p.description !== 'Object' ? `${p.description} ` : ''
  return `${name}{${teile.join(', ')}${p.overflow ? ', …' : ''}}`
}

/** Der Wert als Text, für einfache Werte und die Kopfzeile eines Objekts. */
export function wertText(o: Objekt, oben = false): string {
  switch (o.type) {
    case 'string':
      return oben ? String(o.value) : JSON.stringify(o.value)
    case 'undefined':
      return 'undefined'
    case 'number':
    case 'bigint':
      return o.unserializableValue ?? o.description ?? String(o.value)
    case 'boolean':
      return String(o.value)
    case 'symbol':
      return o.description ?? 'Symbol()'
    case 'function': {
      const kopf = (o.description ?? '').split('{')[0].trim()
      return `ƒ ${kopf.replace(/^function\s*/, '').replace(/^async function\s*/, 'async ')}`
    }
    default:
      if (o.subtype === 'null') return 'null'
      if (o.preview && o.subtype !== 'node' && o.subtype !== 'error') return vorschauText(o.preview)
      return o.description ?? o.className ?? 'Object'
  }
}

function farbe(o: Objekt, oben = false): string {
  // Text in `console.log("…")` ist Meldung, kein Wert.
  if (o.type === 'string') return oben ? '' : FARBE.string
  if (o.type === 'number' || o.type === 'boolean' || o.type === 'bigint') return FARBE.zahl
  if (o.type === 'undefined' || o.subtype === 'null') return FARBE.leer
  if (o.subtype === 'error') return FARBE.fehler
  return ''
}

interface WertProps {
  tab: string
  o: Objekt
  /** Oberste Ebene einer Konsolenzeile: Zeichenketten ohne Anführungszeichen. */
  oben?: boolean
  name?: string
  /** Gleich aufgeklappt zeigen (der lokale Bereich im Debugger). */
  aufgeklappt?: boolean
}

export function Wert({ tab, o, oben = false, name, aufgeklappt = false }: WertProps) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  const [kinder, setKinder] = useState<{ name: string; o: Objekt | null; getter?: boolean }[] | null>(null)
  const aufklappbar = !!o.objectId
  const knoten = o.subtype === 'node'

  const umschalten = async () => {
    if (!offen && !kinder && o.objectId) {
      try {
        const r = await rufen<{ result?: Eigenschaft[]; internalProperties?: Eigenschaft[]; privateProperties?: Eigenschaft[] }>(
          tab,
          'Runtime.getProperties',
          { objectId: o.objectId, ownProperties: true, generatePreview: true },
        )
        const liste = [...(r.result ?? []), ...(r.privateProperties ?? []), ...(r.internalProperties ?? [])]
        setKinder(
          liste.map((p) => ({
            name: p.name === '__proto__' ? '[[Prototype]]' : p.name,
            o: p.value ?? null,
            getter: !p.value && !!p.get && p.get.type !== 'undefined',
          })),
        )
      } catch {
        setKinder([])
      }
    }
    setOffen(!offen)
  }

  useEffect(() => {
    if (aufgeklappt) void umschalten()
    // Nur beim ersten Zeigen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const getterRufen = async (i: number) => {
    if (!o.objectId || !kinder) return
    const k = kinder[i]
    const r = await rufen<{ result?: Objekt }>(tab, 'Runtime.callFunctionOn', {
      objectId: o.objectId,
      functionDeclaration: `function () { return this[${JSON.stringify(k.name)}] }`,
      generatePreview: true,
    }).catch(() => null)
    if (r?.result) setKinder(kinder.map((x, j) => (j === i ? { ...x, o: r.result!, getter: false } : x)))
  }

  return (
    <span className="inline align-top">
      <span
        className={knoten ? 'rounded hover:bg-primary/15' : ''}
        onMouseEnter={knoten && o.objectId ? () => hervorheben(tab, { objectId: o.objectId }) : undefined}
        onMouseLeave={knoten ? () => hervorheben(tab, null) : undefined}
      >
        {aufklappbar ? (
          <button type="button" aria-expanded={offen} onClick={() => void umschalten()} className="text-left">
            <ChevronRight className={`mr-0.5 inline h-3 w-3 transition-transform ${offen ? 'rotate-90' : ''}`} aria-hidden="true" />
            {name !== undefined && <span className={FARBE.name}>{name}: </span>}
            <span className={`${farbe(o, oben)} ${oben ? '' : 'italic'}`}>{wertText(o, oben)}</span>
          </button>
        ) : (
          <>
            {name !== undefined && <span className={FARBE.name}>{name}: </span>}
            <span className={farbe(o, oben)}>{wertText(o, oben)}</span>
          </>
        )}
      </span>
      {knoten && o.objectId && (
        <Kurzinfo text={t('browser.entwickler.imDomZeigen')} lage="oben">
          <button
            type="button"
            aria-label={t('browser.entwickler.imDomZeigen')}
            onClick={() => void aufdecken(tab, { objectId: o.objectId })}
            className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded text-on-surface-variant hover:text-primary"
          >
            <Crosshair className="h-3 w-3" aria-hidden="true" />
          </button>
        </Kurzinfo>
      )}
      {offen && kinder && (
        <span className="block border-l border-outline-variant/50 pl-3">
          {kinder.length === 0 && <span className={`block ${FARBE.leer}`}>{t('browser.entwickler.keineEigenschaften')}</span>}
          {kinder.map((k, i) => (
            <span key={`${k.name}-${i}`} className="block">
              {k.o ? (
                <Wert tab={tab} o={k.o} name={k.name} />
              ) : (
                <>
                  <span className={FARBE.name}>{k.name}: </span>
                  {k.getter ? (
                    <button type="button" className="text-on-surface-variant hover:text-primary" onClick={() => void getterRufen(i)}>
                      (…)
                    </button>
                  ) : (
                    <span className={FARBE.leer}>undefined</span>
                  )}
                </>
              )}
            </span>
          ))}
        </span>
      )}
    </span>
  )
}

/** Ein JSON-Wert aus einer Antwort, aufklappbar wie ein Objekt der Seite. */
export function JsonWert({ wert, name, tiefe = 0 }: { wert: unknown; name?: string; tiefe?: number }) {
  const [offen, setOffen] = useState(tiefe < 1)
  const kopf = name !== undefined && <span className={FARBE.name}>{name}: </span>
  if (wert === null || typeof wert !== 'object') {
    const f = typeof wert === 'string' ? FARBE.string : wert === null ? FARBE.leer : FARBE.zahl
    return (
      <span className="block">
        {kopf}
        <span className={f}>{JSON.stringify(wert)}</span>
      </span>
    )
  }
  const eintraege = Object.entries(wert as Record<string, unknown>)
  const liste = Array.isArray(wert)
  return (
    <span className="block">
      <button type="button" aria-expanded={offen} onClick={() => setOffen(!offen)} className="text-left">
        <ChevronRight className={`mr-0.5 inline h-3 w-3 transition-transform ${offen ? 'rotate-90' : ''}`} aria-hidden="true" />
        {kopf}
        <span className="text-on-surface-variant">{liste ? `Array(${eintraege.length})` : `{${eintraege.length}}`}</span>
      </button>
      {offen && (
        <span className="block border-l border-outline-variant/50 pl-3">
          {eintraege.map(([k, v]) => (
            <JsonWert key={k} wert={v} name={k} tiefe={tiefe + 1} />
          ))}
        </span>
      )}
    </span>
  )
}
