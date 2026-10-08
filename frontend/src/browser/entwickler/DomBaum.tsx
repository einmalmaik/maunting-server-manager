/**
 * Der DOM-Baum der Elemente: aufklappen lädt Kinder nach, Zeigen hebt den
 * Knoten in der Seite hervor, Doppelklick bearbeitet Attribut oder Text. Mit
 * der Tastatur wie ein Baum: Pfeile, Entf löscht, Strg+Z nimmt zurück.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/Singra/UI'

import { useDom, type Knoten } from './domStore'
import { hervorheben, rufen } from './protokoll'

const LEER_BAUM = { wurzel: null, knoten: {}, offen: {}, gewaehlt: null }

export interface Zeile {
  k: Knoten
  tiefe: number
  /** `zu`: die schließende Zeile eines offenen Elements. */
  art: 'auf' | 'zu'
}

/** Ein einziges kurzes Textkind steht in derselben Zeile wie sein Element. */
export function inlineText(knoten: Record<number, Knoten>, k: Knoten): string | null {
  if (k.kinder?.length !== 1 || k.extra.length) return null
  const kind = knoten[k.kinder[0]]
  return kind?.nodeType === 3 && kind.nodeValue.length <= 80 ? kind.nodeValue : null
}

/** Die sichtbaren Zeilen des Baums in Lesereihenfolge. */
export function zeilen(b: { wurzel: number | null; knoten: Record<number, Knoten>; offen: Record<number, true> }): Zeile[] {
  const aus: Zeile[] = []
  const gehen = (id: number, tiefe: number) => {
    const k = b.knoten[id]
    if (!k) return
    // Das Dokument selbst bekommt keine Zeile, nur seine Kinder.
    if (k.nodeType === 9 && k.eltern === null) {
      for (const c of [...k.extra, ...(k.kinder ?? [])]) gehen(c, tiefe)
      return
    }
    aus.push({ k, tiefe, art: 'auf' })
    const offen = b.offen[id] && (k.kinder || k.extra.length) && inlineText(b.knoten, k) === null
    if (!offen) return
    for (const c of [...k.extra, ...(k.kinder ?? [])]) gehen(c, tiefe + 1)
    if (k.nodeType === 1) aus.push({ k, tiefe, art: 'zu' })
  }
  if (b.wurzel !== null) gehen(b.wurzel, 0)
  return aus
}

export async function aufklappen(tab: string, k: Knoten, offen: boolean): Promise<void> {
  if (offen && k.kinder === null) await rufen(tab, 'DOM.requestChildNodes', { nodeId: k.nodeId, depth: 1, pierce: true }).catch(() => null)
  useDom.getState().oeffnen(tab, k.nodeId, offen)
}

/** Ein Feld an Ort und Stelle: Enter oder Wegklicken übernimmt, Escape bricht ab. */
function Feld({ wert, name, fertig }: { wert: string; name: string; fertig: (text: string | null) => void }) {
  const [text, setText] = useState(wert)
  const abbrechen = useRef(false)
  return (
    <span className="inline-block w-60 align-middle" onClick={(e) => e.stopPropagation()}>
      <Input
        autoFocus
        aria-label={name}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') {
            abbrechen.current = true
            e.currentTarget.blur()
          }
        }}
        onBlur={() => fertig(abbrechen.current ? null : text)}
        className="h-6 font-mono text-label-sm"
      />
    </span>
  )
}

/** Ändert den Knoten so, dass Strg+Z es zurücknimmt. */
function aendern(tab: string, methode: string, parameter: Record<string, unknown>) {
  void rufen(tab, 'DOM.markUndoableState')
    .then(() => rufen(tab, methode, parameter))
    .catch(() => null)
}

function Attribut({ tab, k, name, wert }: { tab: string; k: Knoten; name: string; wert: string }) {
  const { t } = useTranslation()
  const [bearbeiten, setBearbeiten] = useState(false)
  if (bearbeiten) {
    return (
      <Feld
        name={t('browser.entwickler.elemente.attributBearbeiten', { name })}
        wert={`${name}="${wert.replace(/"/g, '&quot;')}"`}
        fertig={(text) => {
          setBearbeiten(false)
          if (text !== null) aendern(tab, 'DOM.setAttributesAsText', { nodeId: k.nodeId, name, text })
        }}
      />
    )
  }
  return (
    <span
      onDoubleClick={(e) => {
        e.stopPropagation()
        setBearbeiten(true)
      }}
    >
      {' '}
      <span className="text-secondary">{name}</span>
      {wert !== '' && (
        <>
          =<span className="text-status-success">"{wert}"</span>
        </>
      )}
    </span>
  )
}

function Text({ tab, k }: { tab: string; k: Knoten }) {
  const { t } = useTranslation()
  const [bearbeiten, setBearbeiten] = useState(false)
  if (bearbeiten) {
    return (
      <Feld
        name={t('browser.entwickler.elemente.textBearbeiten')}
        wert={k.nodeValue}
        fertig={(text) => {
          setBearbeiten(false)
          if (text !== null) aendern(tab, 'DOM.setNodeValue', { nodeId: k.nodeId, value: text })
        }}
      />
    )
  }
  return (
    <span className="text-on-surface" onDoubleClick={() => setBearbeiten(true)}>
      "{k.nodeValue}"
    </span>
  )
}

function Inhalt({ tab, z, knoten, offen }: { tab: string; z: Zeile; knoten: Record<number, Knoten>; offen: boolean }) {
  const k = z.k
  const tag = k.localName || k.nodeName.toLowerCase()
  if (z.art === 'zu') return <span className="text-primary">{`</${tag}>`}</span>
  switch (k.nodeType) {
    case 1: {
      if (k.pseudo) return <span className="text-on-surface-variant">::{k.pseudo}</span>
      const text = inlineText(knoten, k)
      // Zugeklappt steht der Inhalt als „…“ in der Zeile, offen darunter.
      const zu = !offen && (k.kinderZahl > 0 || k.extra.length > 0)
      return (
        <>
          <span className="text-primary">{`<${tag}`}</span>
          {k.attribute.map(([n, w]) => (
            <Attribut key={n} tab={tab} k={k} name={n} wert={w} />
          ))}
          <span className="text-primary">{'>'}</span>
          {text !== null ? (
            <>
              <span className="text-on-surface">{text}</span>
              <span className="text-primary">{`</${tag}>`}</span>
            </>
          ) : (
            zu && <span className="text-on-surface-variant">…<span className="text-primary">{`</${tag}>`}</span></span>
          )}
        </>
      )
    }
    case 3:
      return <Text tab={tab} k={k} />
    case 8:
      return <span className="text-on-surface-variant">{`<!--${k.nodeValue}-->`}</span>
    case 10:
      return <span className="text-on-surface-variant">{`<!DOCTYPE ${k.nodeName}>`}</span>
    case 11:
      return <span className="text-on-surface-variant">#shadow-root ({k.schatten ?? 'open'})</span>
    case 9:
      return <span className="text-on-surface-variant">#document</span>
    default:
      return <span className="text-on-surface-variant">{k.nodeName}</span>
  }
}

export function DomBaum({ tab, loeschen }: { tab: string; loeschen: (k: Knoten) => void }) {
  const { t } = useTranslation()
  const baum = useDom((s) => s.baeume[tab] ?? LEER_BAUM)
  const liste = useMemo(() => zeilen(baum), [baum])
  const flaeche = useRef<HTMLDivElement>(null)
  const index = liste.findIndex((z) => z.art === 'auf' && z.k.nodeId === baum.gewaehlt)

  // Der gewählte Knoten bleibt im Blick, auch wenn er von außen kommt (Untersuchen).
  // Jede Wahl (Klick, Suche, Wählen in der Seite) ist `$0` in der Konsole.
  useEffect(() => {
    flaeche.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
    if (baum.gewaehlt !== null) void rufen(tab, 'DOM.setInspectedNode', { nodeId: baum.gewaehlt }).catch(() => null)
  }, [tab, baum.gewaehlt])

  const waehlen = (k: Knoten) => useDom.getState().waehlen(tab, k.nodeId)

  const taste = (e: React.KeyboardEvent) => {
    const z = liste[index]
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const schritt = e.key === 'ArrowDown' ? 1 : -1
      for (let i = index + schritt; i >= 0 && i < liste.length; i += schritt) {
        if (liste[i].art === 'auf') return waehlen(liste[i].k)
      }
    } else if (z && e.key === 'ArrowRight') {
      e.preventDefault()
      if (z.k.kinderZahl > 0 || z.k.extra.length) void aufklappen(tab, z.k, true)
    } else if (z && e.key === 'ArrowLeft') {
      e.preventDefault()
      if (baum.offen[z.k.nodeId]) void aufklappen(tab, z.k, false)
      else if (z.k.eltern !== null && baum.knoten[z.k.eltern]?.nodeType !== 9) waehlen(baum.knoten[z.k.eltern])
    } else if (z && e.key === 'Delete') {
      e.preventDefault()
      loeschen(z.k)
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault()
      void rufen(tab, e.shiftKey ? 'DOM.redo' : 'DOM.undo').catch(() => null)
    }
  }

  return (
    <div
      ref={flaeche}
      role="tree"
      aria-label={t('browser.entwickler.elemente.baum')}
      tabIndex={0}
      onKeyDown={taste}
      onMouseLeave={() => hervorheben(tab, null)}
      className="min-h-0 flex-1 select-text overflow-auto py-1 font-mono text-label-sm focus:outline-none"
    >
      {liste.map((z) => {
        const gewaehlt = z.art === 'auf' && z.k.nodeId === baum.gewaehlt
        const offenBar = z.art === 'auf' && (z.k.kinderZahl > 0 || z.k.extra.length > 0) && inlineText(baum.knoten, z.k) === null
        const offen = !!baum.offen[z.k.nodeId]
        return (
          <div
            key={`${z.k.nodeId}-${z.art}`}
            role="treeitem"
            aria-level={z.tiefe + 1}
            aria-selected={gewaehlt}
            aria-expanded={offenBar ? offen : undefined}
            onClick={() => waehlen(z.k)}
            onMouseEnter={() => hervorheben(tab, { nodeId: z.k.nodeId })}
            className={`flex cursor-default items-start whitespace-nowrap pr-2 ${gewaehlt ? 'bg-primary/20' : 'hover:bg-surface-container-high'}`}
            style={{ paddingLeft: 4 + z.tiefe * 14 }}
          >
            <span className="flex h-4 w-4 shrink-0 items-center justify-center">
              {offenBar && (
                <button
                  type="button"
                  tabIndex={-1}
                  aria-label={offen ? t('browser.entwickler.elemente.zuklappen') : t('browser.entwickler.elemente.aufklappen')}
                  onClick={(e) => {
                    e.stopPropagation()
                    void aufklappen(tab, z.k, !offen)
                  }}
                >
                  <ChevronRight className={`h-3 w-3 transition-transform ${offen ? 'rotate-90' : ''}`} aria-hidden="true" />
                </button>
              )}
            </span>
            <span>
              <Inhalt tab={tab} z={z} knoten={baum.knoten} offen={offen} />
              {gewaehlt && <span className="ml-2 text-on-surface-variant">== $0</span>}
            </span>
          </div>
        )
      })}
    </div>
  )
}
