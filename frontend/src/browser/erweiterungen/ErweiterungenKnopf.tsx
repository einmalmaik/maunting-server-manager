/**
 * Der Puzzle-Knopf der Navigationsleiste und die angehefteten Erweiterungen
 * daneben (nur Windows). Ein Klick auf eine Erweiterung öffnet ihr Popup,
 * ohne Popup ihre Optionen.
 */
import { useRef, useState } from 'react'
import { Pin, PinOff, Puzzle, Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Ankerfenster, Kurzinfo, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { Knopf, KNOPF } from '../kopf/knopf'
import { useTabsStore } from '../services/tabsStore'
import { erweiterungen, fehlerText, useErweiterungen, type Erweiterung } from './erweiterungen'

function Symbol({ e, gross }: { e: Erweiterung; gross?: boolean }) {
  const klasse = gross ? 'h-5 w-5' : 'h-4 w-4'
  return e.angaben.symbol ? (
    <img src={e.angaben.symbol} alt="" className={`${klasse} shrink-0`} />
  ) : (
    <Puzzle className={`${klasse} shrink-0`} aria-hidden="true" />
  )
}

function useAusloesen() {
  const { t } = useTranslation()
  return (e: Erweiterung, anker: HTMLElement) => {
    const lauf = e.angaben.popup ? erweiterungen.popup(e.id, anker) : e.angaben.optionen ? erweiterungen.optionen(e.id) : null
    void lauf?.catch((f) => toast.error(fehlerText(t, f)))
  }
}

function Angeheftet({ e }: { e: Erweiterung }) {
  const anker = useRef<HTMLButtonElement>(null)
  const offen = useErweiterungen((s) => s.popup === e.id)
  const ausloesen = useAusloesen()
  return (
    <Kurzinfo text={e.angaben.name} lage="oben" seite="ende">
      <button
        ref={anker}
        type="button"
        aria-label={e.angaben.name}
        aria-expanded={offen}
        disabled={!e.angaben.popup && !e.angaben.optionen}
        onClick={() => anker.current && ausloesen(e, anker.current)}
        className={KNOPF}
      >
        <Symbol e={e} />
      </button>
    </Kurzinfo>
  )
}

export function ErweiterungenKnopf() {
  const { t } = useTranslation()
  const stand = useErweiterungen((s) => s.stand)
  const anker = useRef<HTMLButtonElement>(null)
  const [offen, setOffen] = useState(false)
  const einstellungen = useTabsStore((s) => s.einstellungen)
  const ausloesen = useAusloesen()
  const name = t('browser.erweiterungen.knopf')
  const laufend = stand?.eintraege.filter((e) => e.laeuft) ?? []
  const fehler = (lauf: Promise<unknown>) => void lauf.catch((f) => toast.error(fehlerText(t, f)))

  return (
    <>
      {laufend
        .filter((e) => e.angeheftet)
        .map((e) => (
          <Angeheftet key={e.id} e={e} />
        ))}
      <Knopf ref={anker} name={name} onClick={() => setOffen((o) => !o)} aria-expanded={offen} aria-haspopup="dialog" seite="ende">
        <Puzzle className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Ankerfenster offen={offen} onSchliessen={() => setOffen(false)} anker={anker} label={name} ausrichtung="ende" className="w-80 p-2">
        {stand?.jugendschutz && <p className="px-2 pb-2 text-label-sm text-on-surface-variant">{t('browser.erweiterungen.jugendschutzKurz')}</p>}
        {stand && stand.eintraege.length === 0 && <p className="px-2 py-2 text-body-sm text-on-surface-variant">{t('browser.erweiterungen.leer')}</p>}
        <ul className="flex flex-col">
          {stand?.eintraege.map((e) => (
            <li key={e.id} className="flex items-center gap-1 rounded-md px-1 hover:bg-surface-container-highest">
              <button
                type="button"
                disabled={!e.laeuft || (!e.angaben.popup && !e.angaben.optionen)}
                onClick={() => {
                  setOffen(false)
                  if (anker.current) ausloesen(e, anker.current)
                }}
                className="flex min-w-0 flex-1 items-center gap-3 px-1 py-2 text-left text-body-sm text-on-surface disabled:opacity-60 [@media(pointer:coarse)]:min-h-11"
              >
                <Symbol e={e} gross />
                <span className="truncate">{e.angaben.name}</span>
              </button>
              <Knopf
                name={e.angeheftet ? t('browser.erweiterungen.losloesen') : t('browser.erweiterungen.anheften')}
                onClick={() => fehler(erweiterungen.anheften(e.id, !e.angeheftet))}
                aktiv={e.angeheftet}
                seite="ende"
              >
                {e.angeheftet ? <PinOff className="h-4 w-4" aria-hidden="true" /> : <Pin className="h-4 w-4" aria-hidden="true" />}
              </Knopf>
              <Switch
                aria-label={t('browser.erweiterungen.schalterName', { name: e.angaben.name })}
                checked={e.an}
                disabled={stand.jugendschutz && !e.an}
                onCheckedChange={(an) => fehler(erweiterungen.schalten(e.id, an))}
              />
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => {
            setOffen(false)
            einstellungen('erweiterungen')
          }}
          className="mt-1 flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-body-sm text-on-surface hover:bg-surface-container-highest [@media(pointer:coarse)]:min-h-11"
        >
          <Settings className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
          {t('browser.erweiterungen.verwalten')}
        </button>
      </Ankerfenster>
    </>
  )
}
