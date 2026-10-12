import { Loader2, Mic } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Kurzinfo } from '@/Singra/UI'
import { useSprachsuche } from '../services/sprachsuche'

interface SprachsucheKnopfProps {
  onErgebnis: (text: string) => void
  onZwischenergebnis?: (text: string) => void
  className?: string
  groesse?: 'sm' | 'md'
}

export function SprachsucheKnopf({
  onErgebnis,
  onZwischenergebnis,
  className = '',
  groesse = 'sm',
}: SprachsucheKnopfProps) {
  const { t } = useTranslation()
  const { umschalten, unterstuetzt, laeuft, transkribiert } = useSprachsuche({
    onErgebnis,
    onZwischenergebnis,
  })

  const dimensionen =
    groesse === 'md'
      ? 'h-8 w-8 [@media(pointer:coarse)]:h-10 [@media(pointer:coarse)]:w-10'
      : 'h-7 w-7 [@media(pointer:coarse)]:h-9 [@media(pointer:coarse)]:w-9'

  const iconGroesse = groesse === 'md' ? 'h-4 w-4' : 'h-3.5 w-3.5'

  const hinweis = transkribiert
    ? t('browser.sprachsuche.transkribiert')
    : laeuft
      ? t('browser.sprachsuche.beenden')
      : unterstuetzt
        ? t('browser.sprachsuche.starten')
        : t('browser.sprachsuche.nichtUnterstuetzt')

  return (
    <Kurzinfo text={hinweis} lage="unten">
      <button
        type="button"
        onClick={umschalten}
        aria-label={hinweis}
        aria-pressed={laeuft}
        disabled={transkribiert}
        className={`relative flex items-center justify-center rounded-full transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${dimensionen} ${
          transkribiert
            ? 'bg-primary/10 text-primary animate-pulse'
            : laeuft
              ? 'bg-status-destructive/15 text-status-destructive animate-pulse ring-2 ring-status-destructive/40'
              : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
        } ${className}`}
      >
        {transkribiert ? (
          <Loader2 className={`${iconGroesse} animate-spin text-primary`} aria-hidden="true" />
        ) : (
          <Mic className={`${iconGroesse} ${laeuft ? 'text-status-destructive' : ''}`} aria-hidden="true" />
        )}
      </button>
    </Kurzinfo>
  )
}
