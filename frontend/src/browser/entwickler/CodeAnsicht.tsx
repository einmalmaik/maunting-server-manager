/**
 * Quelltext mit Zeilennummern, nur die sichtbaren Zeilen gerendert (große
 * Skripte haben Zehntausende). Ein Klick auf die Nummer setzt oder löscht
 * einen Haltepunkt, mit Umschalt eine Bedingung.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

export const ZEILENHOEHE = 18
const PUFFER = 20
/** Länger wird eine einzelne Zeile nicht gezeichnet (verkleinerte Skripte). */
export const ZEILE_MAX = 20_000

interface Props {
  zeilen: string[]
  /** Zeile, an der die Seite steht (ab 0). */
  aktuell?: number | null
  /** Zeile, die angesprungen und markiert wird (ab 0). */
  ziel?: number | null
  haltepunkte?: Map<number, string>
  onNummer?: (zeile: number, bedingung: boolean) => void
}

export function CodeAnsicht({ zeilen, aktuell = null, ziel = null, haltepunkte, onNummer }: Props) {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const [fenster, setFenster] = useState({ oben: 0, hoehe: 400 })
  const breite = String(zeilen.length).length

  useLayoutEffect(() => {
    const el = flaeche.current
    if (!el) return
    const messen = () => setFenster({ oben: el.scrollTop, hoehe: el.clientHeight })
    messen()
    const beobachter = new ResizeObserver(messen)
    beobachter.observe(el)
    return () => beobachter.disconnect()
  }, [])

  // Ziel und aktuelle Zeile kommen in den Blick.
  const springen = ziel ?? aktuell
  useEffect(() => {
    const el = flaeche.current
    if (!el || springen === null) return
    const oben = springen * ZEILENHOEHE
    if (oben < el.scrollTop || oben > el.scrollTop + el.clientHeight - ZEILENHOEHE * 2) {
      el.scrollTop = Math.max(0, oben - el.clientHeight / 3)
    }
  }, [springen, zeilen])

  const von = Math.max(0, Math.floor(fenster.oben / ZEILENHOEHE) - PUFFER)
  const bis = Math.min(zeilen.length, Math.ceil((fenster.oben + fenster.hoehe) / ZEILENHOEHE) + PUFFER)

  return (
    <div
      ref={flaeche}
      onScroll={(e) => setFenster({ oben: e.currentTarget.scrollTop, hoehe: e.currentTarget.clientHeight })}
      className="min-h-0 flex-1 select-text overflow-auto font-mono text-label-sm"
    >
      <div style={{ paddingTop: von * ZEILENHOEHE, paddingBottom: (zeilen.length - bis) * ZEILENHOEHE }} className="min-w-max">
        {zeilen.slice(von, bis).map((text, j) => {
          const i = von + j
          const halt = haltepunkte?.get(i)
          return (
            <div
              key={i}
              style={{ height: ZEILENHOEHE }}
              className={`flex whitespace-pre ${i === aktuell ? 'bg-status-warning/20' : i === ziel ? 'bg-primary/15' : ''}`}
            >
              <button
                type="button"
                aria-label={halt !== undefined ? t('browser.entwickler.quellen.haltepunktWeg', { zeile: i + 1 }) : t('browser.entwickler.quellen.haltepunktSetzen', { zeile: i + 1 })}
                aria-pressed={halt !== undefined}
                disabled={!onNummer}
                onClick={(e) => onNummer?.(i, e.shiftKey)}
                className={`sticky left-0 shrink-0 select-none border-r border-outline-variant/50 pr-2 text-right tabular-nums ${
                  halt !== undefined
                    ? halt
                      ? 'bg-status-warning/70 text-on-surface'
                      : 'bg-primary/70 text-on-primary'
                    : 'bg-surface text-on-surface-variant hover:text-on-surface'
                }`}
                style={{ width: `${breite + 2}ch` }}
              >
                {i + 1}
              </button>
              <span className="pl-2 pr-4 text-on-surface">
                {text.length > ZEILE_MAX ? `${text.slice(0, ZEILE_MAX)} … (${t('browser.entwickler.quellen.zeileGekuerzt', { zahl: text.length })})` : text}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
