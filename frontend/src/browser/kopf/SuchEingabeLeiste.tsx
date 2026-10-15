/**
 * Gemeinsame DNA-Suchleiste des Browsers (`Startseite` und `Maunting Search`).
 *
 * Eine einzige Komponente für die Sucheingabe: mitwachsende Textarea,
 * Suchmaschinen-Auswahl (`SuchwahlKnopf`), Sprachsuche (`SprachsucheKnopf`),
 * Löschen- und Senden-Knopf sowie optionaler Live-Autocomplete-Vorschlagsliste.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Search, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { useAnkerLage } from '@/Singra/UI/Ankerlage'

import { useWirksameSuche } from '../services/einstellungenStore'
import { istAndroid } from '../services/plattform'
import { suchmaschine } from '../services/searchEngines'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { suchName, SuchwahlKnopf } from '../services/suchwahl'
import { SprachsucheKnopf } from './SprachsucheKnopf'

export interface SuchEingabeLeisteProps {
  /** Initialer oder gesteuerter Suchbegriff (z. B. auf der Ergebnisseite). */
  initialWert?: string
  /** Wird beim Absenden oder Auswählen eines Vorschlags gerufen. */
  onSuchen: (begriff: string) => void
  /** Automatischer Fokus beim Öffnen (Standard: auf Desktop ja). */
  autoFocus?: boolean
  /** Kompaktere Höhe für den Header auf der Suchergebnisseite. */
  kompakt?: boolean
  /** Live-Autocomplete über den eigenen Server aktivieren (wenn gekoppelt). */
  autocomplete?: boolean
  /** Aktuelle Sprache/Region für Autocomplete-Vorschläge. */
  sprache?: string
  className?: string
}

export function SuchEingabeLeiste({
  initialWert = '',
  onSuchen,
  autoFocus,
  kompakt = false,
  autocomplete = false,
  sprache = 'de',
  className = '',
}: SuchEingabeLeisteProps) {
  const { t } = useTranslation()
  const [text, setText] = useState(initialWert)
  const [istMehrzeilig, setIstMehrzeilig] = useState(false)
  const [offen, setOffen] = useState(false)
  const [vorschlaege, setVorschlaege] = useState<string[]>([])
  const [markiert, setMarkiert] = useState(-1)

  const huelleRef = useRef<HTMLDivElement>(null)
  const feldRef = useRef<HTMLTextAreaElement>(null)
  const listeRef = useRef<HTMLDivElement>(null)
  const listenId = useId()

  const suche = suchmaschine(useWirksameSuche())
  const gekoppelt = useSitzung((s) => istGekoppelt(s.stand))
  const handy = istAndroid()

  useEffect(() => {
    setText(initialWert)
  }, [initialWert])

  useLayoutEffect(() => {
    const el = feldRef.current
    if (!el) return
    el.style.height = 'auto'
    const scrollH = el.scrollHeight
    const minH = kompakt ? (handy ? 44 : 40) : handy ? 52 : 48
    const neuH = Math.min(192, Math.max(minH, scrollH))
    el.style.height = `${neuH}px`
    setIstMehrzeilig(neuH > minH + 6)
  }, [text, handy, kompakt])

  // Live-Autocomplete vom eigenen Server holen (debounced)
  useEffect(() => {
    if (!autocomplete || !gekoppelt || !offen) {
      setVorschlaege([])
      return
    }
    const q = text.trim()
    if (q.length < 2) {
      setVorschlaege([])
      return
    }
    let aktiv = true
    const timer = window.setTimeout(() => {
      api<{ vorschlaege?: string[] }>('/browser/suche/autocomplete', {
        method: 'POST',
        body: JSON.stringify({ q, sprache: sprache.slice(0, 2) }),
      })
        .then((res) => {
          if (!aktiv) return
          const liste = (res.vorschlaege ?? []).filter((s) => s.toLowerCase() !== q.toLowerCase()).slice(0, 6)
          setVorschlaege(liste)
          setMarkiert(-1)
        })
        .catch(() => {
          if (aktiv) setVorschlaege([])
        })
    }, 160)
    return () => {
      aktiv = false
      window.clearTimeout(timer)
    }
  }, [text, autocomplete, gekoppelt, offen, sprache])

  const zeigeDropdown = offen && vorschlaege.length > 0
  const lage = useAnkerLage(zeigeDropdown, huelleRef, listeRef, { abstand: 6, mindestensAnkerbreite: true })

  useEffect(() => {
    if (!offen) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        huelleRef.current &&
        !huelleRef.current.contains(target) &&
        (!listeRef.current || !listeRef.current.contains(target))
      ) {
        setOffen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [offen])

  const absenden = (zielText: string) => {
    const bereinigt = zielText.trim()
    if (!bereinigt) return
    setOffen(false)
    onSuchen(bereinigt)
  }

  return (
    <form
      className={`w-full ${className}`}
      onSubmit={(e) => {
        e.preventDefault()
        absenden(text)
      }}
    >
      <div
        ref={huelleRef}
        className={`relative flex w-full items-start border border-outline-variant/40 bg-surface-container-low shadow-sm transition-all duration-200 focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/20 hover:bg-surface-container overflow-hidden ${
          istMehrzeilig ? 'rounded-2xl' : 'rounded-full'
        }`}
      >
        <div
          className={`absolute left-2.5 z-10 flex items-center ${
            kompakt ? 'top-1.5 [@media(pointer:coarse)]:top-2' : 'top-2 [@media(pointer:coarse)]:top-2.5'
          }`}
        >
          <SuchwahlKnopf kompakt={kompakt} />
        </div>
        <textarea
          ref={feldRef}
          value={text}
          onFocus={() => setOffen(true)}
          onChange={(e) => {
            setText(e.target.value)
            setOffen(true)
          }}
          onKeyDown={(e) => {
            if (zeigeDropdown && e.key === 'ArrowDown') {
              e.preventDefault()
              setMarkiert((m) => (m + 1) % vorschlaege.length)
              return
            }
            if (zeigeDropdown && e.key === 'ArrowUp') {
              e.preventDefault()
              setMarkiert((m) => (m <= 0 ? vorschlaege.length - 1 : m - 1))
              return
            }
            if (e.key === 'Escape' && zeigeDropdown) {
              e.preventDefault()
              setOffen(false)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              const gewaehlt = markiert >= 0 ? vorschlaege[markiert] : text
              absenden(gewaehlt)
            }
          }}
          rows={1}
          aria-label={t('browser.start.suchen')}
          aria-expanded={zeigeDropdown}
          aria-controls={zeigeDropdown ? listenId : undefined}
          aria-activedescendant={zeigeDropdown && markiert >= 0 ? `${listenId}-${markiert}` : undefined}
          placeholder={t('browser.adresse.platzhalter', { suchmaschine: suchName(suche, t) })}
          className={`w-full resize-none bg-transparent text-body-sm sm:text-body-md text-on-surface placeholder:text-on-surface-variant outline-none max-h-48 overflow-y-auto leading-6 msm-suchleiste-rollbalken transition-[padding] duration-150 ${
            kompakt
              ? 'pl-12 py-2 min-h-[2.5rem] [@media(pointer:coarse)]:min-h-[2.75rem] [@media(pointer:coarse)]:py-2.5'
              : 'pl-[3.875rem] py-3 min-h-[3rem] [@media(pointer:coarse)]:min-h-[3.25rem] [@media(pointer:coarse)]:py-3.5'
          } ${text.length > 0 ? 'pr-28 [@media(pointer:coarse)]:pr-36' : 'pr-12 [@media(pointer:coarse)]:pr-14'}`}
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus ?? (!handy && !kompakt)}
          style={{ fieldSizing: 'content' }}
        />
        <div
          className={`absolute right-2 z-10 flex items-center gap-1 ${
            kompakt ? 'top-1 [@media(pointer:coarse)]:top-1.5' : 'top-2 [@media(pointer:coarse)]:top-2.5'
          }`}
        >
          {text.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setText('')
                setVorschlaege([])
                feldRef.current?.focus()
              }}
              aria-label={t('common.clear')}
              className="flex h-8 w-8 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors [@media(pointer:coarse)]:h-10 [@media(pointer:coarse)]:w-10 animate-fade-in"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          <SprachsucheKnopf
            groesse={kompakt ? 'sm' : 'md'}
            onErgebnis={(sttText) => {
              setText((alt) => (alt ? `${alt.trim()} ${sttText}` : sttText))
              feldRef.current?.focus()
            }}
          />
          {text.length > 0 && (
            <button
              type="submit"
              aria-label={t('browser.start.suchen')}
              className="flex h-8 w-8 items-center justify-center rounded-full text-primary hover:bg-surface-container-highest transition-colors [@media(pointer:coarse)]:h-10 [@media(pointer:coarse)]:w-10 animate-fade-in"
            >
              <Search className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      {zeigeDropdown &&
        createPortal(
          <div
            ref={listeRef}
            id={listenId}
            role="listbox"
            aria-label={t('browser.adresse.vorschlaege')}
            style={lage}
            data-msm-dropdown-menu=""
            className="max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-2xl border border-outline-variant bg-surface-container-high shadow-panel animate-fade-in"
          >
            <div className="divide-y divide-outline-variant/25">
              {vorschlaege.map((v, i) => (
                <button
                  key={v}
                  id={`${listenId}-${i}`}
                  type="button"
                  role="option"
                  aria-selected={i === markiert}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    setText(v)
                    absenden(v)
                  }}
                  onMouseEnter={() => setMarkiert(i)}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-body-sm transition-colors ${
                    i === markiert
                      ? 'bg-surface-container-highest text-primary'
                      : 'text-on-surface hover:bg-surface-container-highest/60'
                  }`}
                >
                  <Search className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
                  <span className="truncate font-medium">{v}</span>
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </form>
  )
}
