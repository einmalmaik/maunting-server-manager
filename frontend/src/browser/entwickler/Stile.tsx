/**
 * Stile des gewählten Elements: Inline-Stil und passende Regeln mit Herkunft,
 * überschriebene Werte durchgestrichen, Geerbtes darunter. Werte lassen sich
 * ändern und abschalten (`CSS.setStyleTexts`), Zustände wie `:hover` erzwingen.
 */
import { useCallback, useEffect, useState } from 'react'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Checkbox, Input } from '@/Singra/UI'

import { elementName, useDom, vorfahren } from './domStore'
import { ortText, rufen } from './protokoll'
import { useAnsicht } from './werkzeuge'

interface Bereich {
  startLine: number
  startColumn: number
  endLine: number
  endColumn: number
}

interface Eigenschaft {
  name: string
  value: string
  important?: boolean
  implicit?: boolean
  text?: string
  disabled?: boolean
  range?: Bereich
}

interface Stil {
  styleSheetId?: string
  cssProperties: Eigenschaft[]
  range?: Bereich
}

interface Regel {
  selectorList: { text: string }
  origin: string
  style: Stil
  styleSheetId?: string
  media?: { text: string }[]
}

interface Passend {
  inlineStyle?: Stil
  matchedCSSRules?: { rule: Regel }[]
  inherited?: { inlineStyle?: Stil; matchedCSSRules: { rule: Regel }[] }[]
}

/** Was vererbt wird; nur das zeigt der Abschnitt „Geerbt von“. */
const VERERBT = /^(--|color$|cursor$|direction$|font|letter-spacing$|line-height$|list-style|quotes$|text-|visibility$|white-space$|word-|writing-mode$|tab-size$|hyphens$|caret-color$|accent-color$|color-scheme$)/

const ZUSTAENDE = ['hover', 'active', 'focus', 'focus-visible', 'focus-within', 'visited'] as const

/**
 * Die Eigenschaften, die im Quelltext stehen (ohne ausgeschriebene Langformen).
 * Regeln des Browsers haben keinen Quelltext; dort zählt jede Eigenschaft.
 */
const sichtbare = (s: Stil) => s.cssProperties.filter((p) => !p.implicit && (!s.range || p.range || p.text !== undefined || p.disabled))

/** Der neue Text eines Stils nach einer Änderung an Stelle `i` (`null` = entfernen). */
export function stilText(props: Eigenschaft[], i: number, neu: { name: string; value: string; disabled?: boolean } | null): string {
  const liste = props.map((p) => ({ name: p.name, value: p.value + (p.important && !/!important/.test(p.value) ? ' !important' : ''), disabled: !!p.disabled }))
  if (neu === null) liste.splice(i, 1)
  else if (i >= liste.length) liste.push({ disabled: false, ...neu })
  else liste[i] = { ...liste[i], ...neu }
  return liste.map((p) => (p.disabled ? `/* ${p.name}: ${p.value}; */` : `${p.name}: ${p.value};`)).join(' ')
}

interface AbschnittProps {
  tab: string
  titel: string
  ort?: { url: string; zeile: number }
  stil: Stil
  ueberschrieben: Set<Eigenschaft>
  nurVererbt?: boolean
  neuLaden: () => void
}

function Abschnitt({ tab, titel, ort, stil, ueberschrieben, nurVererbt, neuLaden }: AbschnittProps) {
  const { t } = useTranslation()
  const setZiel = useAnsicht((s) => s.setZiel)
  const [bearbeitet, setBearbeitet] = useState<number | null>(null)
  const [text, setText] = useState('')
  const props = sichtbare(stil)
  const zeigen = nurVererbt ? props.filter((p) => VERERBT.test(p.name)) : props
  const aenderbar = !!stil.styleSheetId && !!stil.range
  if (zeigen.length === 0 && nurVererbt) return null

  const schreiben = (i: number, neu: { name: string; value: string; disabled?: boolean } | null) => {
    if (!stil.styleSheetId || !stil.range) return
    void rufen(tab, 'CSS.setStyleTexts', { edits: [{ styleSheetId: stil.styleSheetId, range: stil.range, text: stilText(props, i, neu) }] })
      .catch(() => null)
      .then(neuLaden)
  }
  const fertig = () => {
    const i = bearbeitet
    setBearbeitet(null)
    if (i === null) return
    const m = /^\s*([-\w]+)\s*:\s*(.*?);?\s*$/.exec(text)
    if (!m) return text.trim() === '' && i < props.length ? schreiben(i, null) : undefined
    schreiben(i, { name: m[1], value: m[2] })
  }

  return (
    <section className="border-b border-outline-variant/50 py-1 font-mono text-label-sm">
      <div className="flex items-start gap-2 px-3">
        <span className="min-w-0 flex-1 break-all text-on-surface">{titel} {'{'}</span>
        {ort && (
          <button type="button" onClick={() => setZiel(ort)} className="shrink-0 font-sans text-on-surface-variant hover:text-primary hover:underline">
            {ortText(ort.url, ort.zeile)}
          </button>
        )}
      </div>
      {zeigen.map((p) => {
        const i = props.indexOf(p)
        if (bearbeitet === i) {
          return (
            <div key={i} className="px-6">
              <Input autoFocus aria-label={t('browser.entwickler.stile.wert')} value={text} onChange={(e) => setText(e.target.value)} onBlur={fertig} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} className="h-6 font-mono text-label-sm" />
            </div>
          )
        }
        const durch = ueberschrieben.has(p) || p.disabled
        return (
          <div key={i} className="group flex items-center gap-1.5 pl-3 pr-3">
            {aenderbar ? (
              <Checkbox
                checked={!p.disabled}
                onCheckedChange={(an) => schreiben(i, { name: p.name, value: p.value, disabled: !an })}
                aria-label={t('browser.entwickler.stile.umschalten', { name: p.name })}
                className="scale-75"
              />
            ) : (
              <span className="w-4" />
            )}
            <span
              className={`min-w-0 flex-1 break-all ${durch ? 'line-through opacity-60' : ''} ${aenderbar ? 'cursor-text' : ''}`}
              onDoubleClick={() => {
                if (!aenderbar) return
                setText(`${p.name}: ${p.value}`)
                setBearbeitet(i)
              }}
            >
              <span className="text-secondary">{p.name}</span>: <span className="text-on-surface">{p.value}</span>
              {p.important && !/!important/.test(p.value) && <span className="text-on-surface"> !important</span>};
            </span>
          </div>
        )
      })}
      {aenderbar && bearbeitet === props.length ? (
        <div className="px-6">
          <Input autoFocus aria-label={t('browser.entwickler.stile.neu')} placeholder="name: wert" value={text} onChange={(e) => setText(e.target.value)} onBlur={fertig} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} className="h-6 font-mono text-label-sm" />
        </div>
      ) : (
        aenderbar && (
          <button
            type="button"
            onClick={() => {
              setText('')
              setBearbeitet(props.length)
            }}
            className="ml-6 flex items-center gap-1 font-sans text-on-surface-variant hover:text-primary"
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            {t('browser.entwickler.stile.neu')}
          </button>
        )
      )}
      <div className="px-3 text-on-surface">{'}'}</div>
    </section>
  )
}

/** Welche Werte von einem gewichtigeren überdeckt werden. Reihenfolge: gewichtigste zuerst. */
export function ueberdeckt(stile: Stil[]): Set<Eigenschaft> {
  const aus = new Set<Eigenschaft>()
  const gesehen = new Map<string, Eigenschaft>()
  for (const wichtig of [true, false]) {
    for (const s of stile) {
      for (const p of s.cssProperties) {
        if (p.disabled || !!p.important !== wichtig) continue
        if (gesehen.has(p.name)) aus.add(p)
        else gesehen.set(p.name, p)
      }
    }
  }
  return aus
}

export function Stile({ tab, nodeId }: { tab: string; nodeId: number }) {
  const { t } = useTranslation()
  const blaetter = useDom((s) => s.blaetter[tab])
  const baum = useDom((s) => s.baeume[tab])
  const [daten, setDaten] = useState<Passend | null>(null)
  const [zustaende, setZustaende] = useState<string[]>([])

  const laden = useCallback(() => {
    rufen<Passend>(tab, 'CSS.getMatchedStylesForNode', { nodeId })
      .then(setDaten)
      .catch(() => setDaten({}))
  }, [tab, nodeId])
  useEffect(() => {
    setZustaende([])
    laden()
  }, [laden])

  const erzwingen = (z: string, an: boolean) => {
    const neu = an ? [...zustaende, z] : zustaende.filter((x) => x !== z)
    setZustaende(neu)
    void rufen(tab, 'CSS.forcePseudoState', { nodeId, forcedPseudoClasses: neu }).catch(() => null).then(laden)
  }

  if (!daten) return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.laedt')}</p>
  const regeln = [...(daten.matchedCSSRules ?? [])].reverse().map((m) => m.rule)
  const eigene: Stil[] = [...(daten.inlineStyle ? [daten.inlineStyle] : []), ...regeln.map((r) => r.style)]
  const geerbt = (daten.inherited ?? []).map((e) => ({ inline: e.inlineStyle, regeln: [...e.matchedCSSRules].reverse().map((m) => m.rule) }))
  const alle = [...eigene, ...geerbt.flatMap((g) => [...(g.inline ? [g.inline] : []), ...g.regeln.map((r) => r.style)].map((s) => ({ ...s, cssProperties: s.cssProperties.filter((p) => VERERBT.test(p.name)) })))]
  // Die gefilterten Kopien teilen sich die Eigenschaften mit den Originalen.
  const ueberschrieben = ueberdeckt(alle)
  const ahnen = baum ? vorfahren(baum, nodeId).filter((k) => k.nodeType === 1).reverse().slice(1) : []
  const ort = (r: Regel) => {
    const url = r.styleSheetId ? blaetter?.[r.styleSheetId] : undefined
    return url && r.style.range ? { url, zeile: r.style.range.startLine } : undefined
  }
  const herkunft = (r: Regel) => (r.origin === 'user-agent' ? ` ${t('browser.entwickler.stile.browser')}` : '')

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div role="group" aria-label={t('browser.entwickler.stile.zustaende')} className="flex flex-wrap gap-x-3 gap-y-1 border-b border-outline-variant/50 px-3 py-1.5 text-label-sm">
        {ZUSTAENDE.map((z) => (
          <label key={z} className="flex items-center gap-1 font-mono text-on-surface-variant">
            <Checkbox checked={zustaende.includes(z)} onCheckedChange={(an) => erzwingen(z, an)} />:{z}
          </label>
        ))}
      </div>
      {daten.inlineStyle && <Abschnitt tab={tab} titel="element.style" stil={daten.inlineStyle} ueberschrieben={ueberschrieben} neuLaden={laden} />}
      {regeln.map((r, i) => (
        <Abschnitt key={i} tab={tab} titel={`${r.media?.length ? `@media ${r.media[0].text} ` : ''}${r.selectorList.text}${herkunft(r)}`} ort={ort(r)} stil={r.style} ueberschrieben={ueberschrieben} neuLaden={laden} />
      ))}
      {geerbt.map((g, i) => (
        <div key={`g-${i}`}>
          <h3 className="bg-surface-container-low px-3 py-1 text-label-sm text-on-surface-variant">
            {t('browser.entwickler.stile.geerbt')} <span className="font-mono">{ahnen[i] ? elementName(ahnen[i]) : ''}</span>
          </h3>
          {g.inline && <Abschnitt tab={tab} titel="style" stil={g.inline} ueberschrieben={ueberschrieben} nurVererbt neuLaden={laden} />}
          {g.regeln.map((r, j) => (
            <Abschnitt key={`${i}-${j}`} tab={tab} titel={`${r.selectorList.text}${herkunft(r)}`} ort={ort(r)} stil={r.style} ueberschrieben={ueberschrieben} nurVererbt neuLaden={laden} />
          ))}
        </div>
      ))}
    </div>
  )
}
