/**
 * Der Rahmen der Leisten über der Seite (Anmeldung, Zahlung, Übersetzung):
 * Symbol, Text, Knöpfe und „Nicht jetzt“.
 */
import type { ReactNode } from 'react'
import { KeyRound, X, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

import { useKlickSperre } from './klickSperre'

interface Props {
  text: string
  onSchliessen: () => void
  /** Name der Region für Screenreader; ohne Angabe „Anmeldung“. */
  name?: string
  symbol?: LucideIcon
  /** Name des Schließknopfs; ohne Angabe „Nicht jetzt“. */
  schliessenText?: string
  children?: ReactNode
}

export function Leiste({ text, onSchliessen, name, symbol: Symbol = KeyRound, schliessenText, children }: Props) {
  const { t } = useTranslation()
  const schliessen = schliessenText ?? t('browser.formular.nichtJetzt')
  const sperre = useKlickSperre(text)
  return (
    <div role="region" aria-label={name ?? t('browser.formular.leiste')} onClickCapture={sperre} className="flex shrink-0 flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-container-high px-3 py-1.5">
      <Symbol className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
      <p className="min-w-0 flex-1 text-body-sm text-on-surface">{text}</p>
      {children}
      <Kurzinfo text={schliessen} lage="oben" seite="ende">
        <button
          type="button"
          onClick={onSchliessen}
          aria-label={schliessen}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-highest [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </Kurzinfo>
    </div>
  )
}
