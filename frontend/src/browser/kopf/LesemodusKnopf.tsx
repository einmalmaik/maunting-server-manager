/**
 * Omnibox-Schaltfläche für den puristischen Lesemodus:
 * Erscheint dezent, sobald ein lesbarer Artikel erkannt wurde oder der
 * Lesemodus aktiv ist. Schaltet per Klick oder Tastenkürzel F9 um.
 */
import { BookOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Kurzinfo } from '@/Singra/UI'

import type { Tab } from '../services/tab'
import { istWebseite } from '../services/intern'
import { useTabsStore } from '../services/tabsStore'

interface LesemodusKnopfProps {
  tab?: Tab
  className?: string
  groesse?: 'sm' | 'md'
}

export function LesemodusKnopf({ tab, className = '', groesse = 'sm' }: LesemodusKnopfProps) {
  const { t } = useTranslation()
  const lesemodusUmschalten = useTabsStore((s) => s.lesemodusUmschalten)

  // Auf jeder HTTP(S)-Seite anbieten: die Lesbarkeitsprüfung kann scheitern,
  // bevor dynamisch geladene Inhalte verfügbar sind.
  if (!tab || !istWebseite(tab.url)) {
    return null
  }

  const aktiv = Boolean(tab.lesemodus)
  const dimensionen =
    groesse === 'md'
      ? 'h-8 w-8 [@media(pointer:coarse)]:h-10 [@media(pointer:coarse)]:w-10'
      : 'h-7 w-7 [@media(pointer:coarse)]:h-9 [@media(pointer:coarse)]:w-9'
  const iconGroesse = groesse === 'md' ? 'h-4 w-4' : 'h-3.5 w-3.5'

  const hinweis = aktiv
    ? `${t('browser.lesemodus.beenden')} ${t('browser.lesemodus.kuerzelHinweis')}`
    : `${t('browser.lesemodus.aktivieren')} ${t('browser.lesemodus.kuerzelHinweis')}`

  return (
    <Kurzinfo text={hinweis} lage="unten">
      <button
        type="button"
        onClick={() => void lesemodusUmschalten(tab.id)}
        aria-label={hinweis}
        aria-pressed={aktiv}
        className={`relative flex items-center justify-center rounded-full transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${dimensionen} ${
          aktiv
            ? 'bg-primary/15 text-primary'
            : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
        } ${className}`}
      >
        <BookOpen className={iconGroesse} aria-hidden="true" />
      </button>
    </Kurzinfo>
  )
}
