/**
 * Die Adresszeile. Zeigt die Adresse des vorderen Tabs; getippt wird eine
 * Adresse oder ein Suchbegriff. Vorschläge kommen nur aus Lesezeichen und
 * Verlauf auf diesem Gerät (`vorschlaege`), nie von einer Suchmaschine.
 */
import { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Bookmark, Clock, Globe, Lock, LockOpen, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { toast } from '@/stores/toastStore'
import { useAnkerLage } from '@/Singra/UI/Ankerlage'
import { Omnibox } from '@/Singra/UI'

import { useEinstellungenStore, useWirksameSuche } from '../services/einstellungenStore'
import { msmSucheBegriff } from '../services/intern'
import { bereinigeUrl } from '../services/linkReiniger'
import { nativ } from '../services/nativ'
import { baueZielUrl, istAdresse, suchmaschine } from '../services/searchEngines'
import { suchName, SuchwahlKnopf } from '../services/suchwahl'
import { SeitenSicherheitPopover } from './SeitenSicherheitPopover'
import { SprachsucheKnopf } from './SprachsucheKnopf'
import { LesemodusKnopf } from './LesemodusKnopf'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'
import { useVerlaufStore, vorschlaege, type Eintrag } from '../services/verlaufStore'

export interface AdresszeileGriff {
  fokussieren: () => void
}

interface Vorschlag {
  art: 'eingabe' | 'lesezeichen' | 'verlauf'
  url: string
  titel: string
}

export const Adresszeile = forwardRef<AdresszeileGriff>(function Adresszeile(_, griff) {
  const { t } = useTranslation()
  const tab = useAktiverTab()
  const eingeben = useTabsStore((s) => s.eingeben)
  const oeffnen = useTabsStore((s) => s.oeffnen)
  const suche = suchmaschine(useWirksameSuche())
  const searxngUrl = useEinstellungenStore((s) => s.searxngUrl)
  const cleanCopy = useEinstellungenStore((s) => s.cleanCopy)
  const verlauf = useVerlaufStore((s) => s.verlauf)
  const lesezeichen = useVerlaufStore((s) => s.lesezeichen)

  const feld = useRef<HTMLInputElement>(null)
  const huelle = useRef<HTMLDivElement>(null)
  const schlossKnopf = useRef<HTMLButtonElement>(null)
  const liste = useRef<HTMLUListElement>(null)
  const listenId = useId()
  const [text, setText] = useState('')
  const [tippt, setTippt] = useState(false)
  const [markiert, setMarkiert] = useState(0)
  const [sicherheitOffen, setSicherheitOffen] = useState(false)

  // Bei der MSM-Suche steht der Begriff da, nicht `msb://suche?q=…`.
  const begriff = tab ? msmSucheBegriff(tab.url) : null
  const adresse = begriff ?? tab?.url ?? ''
  useEffect(() => {
    if (!tippt) setText(adresse)
  }, [adresse, tippt, tab?.id])

  useImperativeHandle(griff, () => ({
    fokussieren: () => {
      void nativ.oberflaecheFokussieren()
      feld.current?.focus()
      feld.current?.select()
    },
  }))

  const treffer: Vorschlag[] = tippt && text.trim()
    ? [
        { art: 'eingabe' as const, url: baueZielUrl(text, suche.id, searxngUrl) ?? '', titel: text.trim() },
        ...vorschlaege(text, verlauf, lesezeichen, 6).map((e: Eintrag) => ({
          art: lesezeichen.some((l) => l.url === e.url) ? ('lesezeichen' as const) : ('verlauf' as const),
          url: e.url,
          titel: e.titel,
        })),
      ]
    : []
  // Nur mit Treffern aus Verlauf oder Lesezeichen: die Liste verdeckt die
  // Seite (`ueberdeckung.ts`), und für „Suchen nach …“ allein lohnt das nicht.
  const offen = treffer.length > 1
  const lage = useAnkerLage(offen, huelle, liste, { abstand: 4, mindestensAnkerbreite: true })

  const beenden = () => {
    setTippt(false)
    setMarkiert(0)
  }

  const waehlen = (v: Vorschlag) => {
    beenden()
    if (v.art === 'eingabe') eingeben(v.titel)
    else oeffnen(v.url)
    feld.current?.blur()
  }

  const [suchwahlOffen, setSuchwahlOffen] = useState(false)

  const sicher = adresse.startsWith('https://')
  const unsicher = adresse.startsWith('http://')
  const zeigeSicherheit = Boolean(adresse && !tippt && !begriff)

  return (
    <Omnibox
      ref={huelle}
      links={
        <>
          <SuchwahlKnopf kompakt beiOffenAenderung={setSuchwahlOffen} />
          {zeigeSicherheit && (
            <>
              <button
                ref={schlossKnopf}
                type="button"
                onClick={() => setSicherheitOffen((o) => !o)}
                aria-label={t(sicher ? 'browser.adresse.sicher' : 'browser.adresse.unsicher')}
                className="flex h-5 w-5 items-center justify-center rounded hover:bg-surface-container-highest transition-colors text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary"
              >
                {sicher ? (
                  <Lock className="h-3.5 w-3.5 text-status-success" role="img" aria-label={t('browser.adresse.sicher')} />
                ) : unsicher ? (
                  <LockOpen className="h-3.5 w-3.5 text-status-warning" role="img" aria-label={t('browser.adresse.unsicher')} />
                ) : (
                  <Globe className="h-3.5 w-3.5" aria-hidden="true" />
                )}
              </button>
              <SeitenSicherheitPopover
                offen={sicherheitOffen}
                onSchliessen={() => setSicherheitOffen(false)}
                anker={schlossKnopf}
                tab={tab}
              />
            </>
          )}
        </>
      }
      rechts={
        <>
          {!tippt && tab?.status && (
            <span className="pointer-events-none max-w-[200px] truncate rounded-full bg-surface-container-high px-2 py-0.5 text-label-sm text-on-surface-variant">
              {tab.status}
            </span>
          )}
          <LesemodusKnopf tab={tab} />
          <SprachsucheKnopf
            onErgebnis={(sttText) => {
              setText((alt) => (alt ? `${alt.trim()} ${sttText}` : sttText))
              setTippt(true)
              feld.current?.focus()
            }}
          />
        </>
      }
    >
      {/* Eingabefeld im flexiblen Raum */}
      <input
        ref={feld}
        value={text}
        aria-label={t('browser.adresse.label')}
        placeholder={t('browser.adresse.platzhalter', { suchmaschine: suchName(suche, t) })}
        spellCheck={false}
        autoComplete="off"
        role="combobox"
        aria-expanded={offen}
        aria-controls={listenId}
        aria-activedescendant={offen ? `${listenId}-${markiert}` : undefined}
        className="h-full min-w-0 flex-1 bg-transparent text-body-sm text-on-surface placeholder:text-on-surface-variant outline-none p-0"
        onFocus={(e) => {
          e.currentTarget.select()
          setTippt(true)
        }}
        onBlur={(e) => {
          const naechstes = e.relatedTarget as Node | null
          if (huelle.current?.contains(naechstes) || suchwahlOffen) {
            return
          }
          setTimeout(() => {
            if (!suchwahlOffen && document.activeElement !== feld.current) {
              beenden()
            }
          }, 120)
        }}
        onChange={(e) => {
          setText(e.target.value)
          setTippt(true)
          setMarkiert(0)
        }}
        onCopy={(e) => {
          if (!cleanCopy) return
          const selection = window.getSelection()?.toString() || text
          if (!selection) return
          const ergebnis = bereinigeUrl(selection)
          if (ergebnis.bereinigt) {
            e.preventDefault()
            e.clipboardData.setData('text/plain', ergebnis.url)
            toast.success(t('browser.adresse.linkBereinigtKopiert'))
          }
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && offen) {
            e.preventDefault()
            setMarkiert((m) => (m + 1) % treffer.length)
          } else if (e.key === 'ArrowUp' && offen) {
            e.preventDefault()
            setMarkiert((m) => (m - 1 + treffer.length) % treffer.length)
          } else if (e.key === 'Enter') {
            e.preventDefault()
            const v = treffer[markiert]
            if (v) waehlen(v)
            else if (text.trim()) waehlen({ art: 'eingabe', url: '', titel: text })
          } else if (e.key === 'Escape') {
            e.preventDefault()
            beenden()
            setText(adresse)
          }
        }}
      />
      {offen &&

        createPortal(
          <ul
            ref={liste}
            id={listenId}
            role="listbox"
            aria-label={t('browser.adresse.vorschlaege')}
            style={lage}
            className="max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-lg border border-outline-variant bg-surface-container-high py-1 shadow-xl"
          >
            {treffer.map((v, i) => (
              <li
                key={`${v.art}-${v.url}`}
                id={`${listenId}-${i}`}
                role="option"
                aria-selected={i === markiert}
                onPointerDown={(e) => {
                  e.preventDefault()
                  waehlen(v)
                }}
                onPointerEnter={() => setMarkiert(i)}
                className={`flex cursor-default items-center gap-3 px-3 py-2 text-body-sm ${
                  i === markiert ? 'bg-surface-container-highest' : ''
                }`}
              >
                {v.art === 'eingabe' ? (
                  istAdresse(v.titel) ? <Globe className="h-4 w-4 shrink-0" aria-hidden="true" /> : <Search className="h-4 w-4 shrink-0" aria-hidden="true" />
                ) : v.art === 'lesezeichen' ? (
                  <Bookmark className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                ) : (
                  <Clock className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                )}
                <span className="min-w-0 flex-1 truncate">
                  {v.art === 'eingabe'
                    ? istAdresse(v.titel)
                      ? v.url
                      : t('browser.adresse.suchenMit', { begriff: v.titel, suchmaschine: suchName(suche, t) })
                    : v.titel}
                </span>
                {v.art !== 'eingabe' && <span className="max-w-[40%] truncate text-label-sm text-on-surface-variant">{v.url}</span>}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </Omnibox>
  )
})

