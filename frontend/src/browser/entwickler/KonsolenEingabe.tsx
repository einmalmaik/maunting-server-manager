/**
 * Die Eingabe der Konsole: Enter führt aus, Umschalt+Enter bricht um, Pfeile
 * blättern im Verlauf. Beim Tippen schlägt sie Namen vor (globale Namen oder
 * Eigenschaften des Objekts vor dem Punkt), ausgewertet ohne Nebenwirkung.
 * Ist die Seite im Debugger angehalten, gilt der gewählte Rahmen.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Textarea } from '@/Singra/UI'

import { useKonsole } from './konsoleStore'
import { rufen, type Objekt } from './protokoll'
import { useQuellen } from './quellenStore'

const VORSCHLAEGE_MAX = 50

/** Was vor dem Cursor steht: Objektpfad (oder leer) und angefangener Name. */
export function zerlegen(text: string): { pfad: string; anfang: string } | null {
  const m = /((?:[A-Za-z_$][\w$]*\s*\.\s*)*)([A-Za-z_$][\w$]*)?$/.exec(text)
  if (!m || (!m[1] && !m[2])) return null
  return { pfad: m[1].replace(/\s*\.\s*$/, '').replace(/\s+/g, ''), anfang: m[2] ?? '' }
}

async function namen(tab: string, pfad: string): Promise<string[]> {
  const ziel = pfad || 'globalThis'
  const ausdruck = `(() => { const n = new Set(); for (let o = (${ziel}); o != null; o = Object.getPrototypeOf(o)) Object.getOwnPropertyNames(o).forEach((x) => n.add(x)); return [...n] })()`
  const r = await rufen<{ result?: Objekt }>(tab, 'Runtime.evaluate', { expression: ausdruck, returnByValue: true, throwOnSideEffect: true, silent: true })
  const liste = Array.isArray(r.result?.value) ? (r.result!.value as string[]) : []
  if (!pfad) {
    const lexikalisch = await rufen<{ names?: string[] }>(tab, 'Runtime.globalLexicalScopeNames').catch(() => ({ names: [] }))
    liste.push(...(lexikalisch.names ?? []))
  }
  return liste.filter((n) => /^[A-Za-z_$][\w$]*$/.test(n))
}

export async function auswerten(tab: string, ausdruck: string): Promise<{ wert: Objekt; fehler: boolean }> {
  const halt = useQuellen.getState().halt[tab]
  const r = halt
    ? await rufen<{ result: Objekt; exceptionDetails?: { text: string; exception?: Objekt } }>(tab, 'Debugger.evaluateOnCallFrame', {
        callFrameId: halt.rahmen[halt.gewaehlt]?.callFrameId,
        expression: ausdruck,
        objectGroup: 'konsole',
        includeCommandLineAPI: true,
        generatePreview: true,
      })
    : await rufen<{ result: Objekt; exceptionDetails?: { text: string; exception?: Objekt } }>(tab, 'Runtime.evaluate', {
        expression: ausdruck,
        objectGroup: 'konsole',
        replMode: true,
        includeCommandLineAPI: true,
        generatePreview: true,
        userGesture: true,
        awaitPromise: true,
      })
  if (r.exceptionDetails) {
    return { wert: r.exceptionDetails.exception ?? { type: 'string', value: r.exceptionDetails.text }, fehler: true }
  }
  return { wert: r.result ?? { type: 'undefined' }, fehler: false }
}

export function KonsolenEingabe({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [vorschlaege, setVorschlaege] = useState<string[]>([])
  const [auswahl, setAuswahl] = useState(0)
  const [blaettern, setBlaettern] = useState<number | null>(null)
  const feld = useRef<HTMLTextAreaElement>(null)
  const lauf = useRef(0)

  // Vorschläge nach einer kurzen Pause im Tippen.
  useEffect(() => {
    const teile = zerlegen(text)
    if (!teile || (!teile.anfang && !teile.pfad)) {
      setVorschlaege([])
      return
    }
    const nr = ++lauf.current
    const zeit = setTimeout(() => {
      void namen(tab, teile.pfad)
        .then((alle) => {
          if (nr !== lauf.current) return
          const passend = [...new Set(alle)]
            .filter((n) => n.startsWith(teile.anfang) && n !== teile.anfang)
            .sort((a, b) => a.length - b.length || a.localeCompare(b))
            .slice(0, VORSCHLAEGE_MAX)
          setVorschlaege(passend)
          setAuswahl(0)
        })
        .catch(() => setVorschlaege([]))
    }, 150)
    return () => clearTimeout(zeit)
  }, [tab, text])

  const uebernehmen = (name: string) => {
    const teile = zerlegen(text)
    if (!teile) return
    setText(text.slice(0, text.length - teile.anfang.length) + name)
    setVorschlaege([])
  }

  const ausfuehren = async () => {
    const ausdruck = text.trim()
    if (!ausdruck) return
    setText('')
    setVorschlaege([])
    setBlaettern(null)
    const { eingabe, ergebnis } = useKonsole.getState()
    eingabe(tab, ausdruck)
    try {
      const { wert, fehler } = await auswerten(tab, ausdruck)
      ergebnis(tab, wert, fehler)
    } catch (fehler) {
      ergebnis(tab, { type: 'string', value: String(fehler) }, true)
    }
  }

  const taste = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const liste = vorschlaege.length > 0
    if (liste && e.key === 'Tab') {
      e.preventDefault()
      uebernehmen(vorschlaege[auswahl])
      return
    }
    if (liste && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault()
      setAuswahl((a) => (a + (e.key === 'ArrowDown' ? 1 : vorschlaege.length - 1)) % vorschlaege.length)
      return
    }
    if (liste && e.key === 'Escape') {
      e.preventDefault()
      setVorschlaege([])
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void ausfuehren()
      return
    }
    // Verlauf nur, wenn der Cursor in der ersten bzw. letzten Zeile steht.
    const el = e.currentTarget
    const verlauf = useKonsole.getState().verlauf
    if (e.key === 'ArrowUp' && !el.value.slice(0, el.selectionStart).includes('\n') && verlauf.length) {
      e.preventDefault()
      const i = blaettern === null ? verlauf.length - 1 : Math.max(0, blaettern - 1)
      setBlaettern(i)
      setText(verlauf[i])
    } else if (e.key === 'ArrowDown' && blaettern !== null && !el.value.slice(el.selectionEnd).includes('\n')) {
      e.preventDefault()
      const i = blaettern + 1
      setBlaettern(i < verlauf.length ? i : null)
      setText(i < verlauf.length ? verlauf[i] : '')
    }
  }

  return (
    <div className="relative border-t border-outline-variant p-2">
      {vorschlaege.length > 0 && (
        <ul
          role="listbox"
          aria-label={t('browser.entwickler.konsole.vorschlaege')}
          className="absolute bottom-full left-2 right-2 z-10 max-h-48 overflow-y-auto rounded-md border border-outline-variant bg-surface-container-high py-1 font-mono text-label-sm shadow-lg"
        >
          {vorschlaege.map((v, i) => (
            <li
              key={v}
              role="option"
              aria-selected={i === auswahl}
              onMouseDown={(e) => {
                e.preventDefault()
                uebernehmen(v)
                feld.current?.focus()
              }}
              className={`cursor-pointer px-3 py-0.5 ${i === auswahl ? 'bg-primary/15 text-on-surface' : 'text-on-surface-variant'}`}
            >
              {v}
            </li>
          ))}
        </ul>
      )}
      <Textarea
        ref={feld}
        aria-label={t('browser.entwickler.konsole.eingabe')}
        placeholder={t('browser.entwickler.konsole.eingabePlatzhalter')}
        value={text}
        rows={Math.min(6, text.split('\n').length)}
        onChange={(e) => {
          setText(e.target.value)
          setBlaettern(null)
        }}
        onKeyDown={taste}
        className="!min-h-0 resize-none font-mono text-label-sm placeholder:truncate"
        spellCheck={false}
        autoComplete="off"
      />
    </div>
  )
}
