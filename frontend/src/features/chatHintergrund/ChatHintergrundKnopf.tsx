import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Image as ImageIcon } from 'lucide-react'

import { Button } from '@/Singra/UI'

import { ChatHintergrundDialog } from './ChatHintergrundDialog'
import type { ChatHintergrundBereich } from './speicher'

/**
 * Der Knopf, der das Hintergrundfenster öffnet — samt Fenster.
 *
 * Wer ihn setzt, braucht keinen eigenen Zustand und keine zweite Einbaustelle:
 * eine Zeile, und die Fläche ist einstellbar. Der Messenger benutzt ihn nicht,
 * weil sein Einstieg im Blattmenü sitzt und dessen Zeilen anders aussehen — er
 * öffnet dasselbe `ChatHintergrundDialog` von dort. Gleiches Zeichen, gleiche
 * Beschriftung, gleiches Fenster.
 */
export function ChatHintergrundKnopf({
  bereich,
  className = '',
}: {
  bereich: ChatHintergrundBereich
  className?: string
}) {
  const { t } = useTranslation()
  const [offen, setzeOffen] = useState(false)

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => setzeOffen(true)}
        aria-label={t('social.wallpaper.title')}
        title={t('social.wallpaper.title')}
        className={`h-8 w-8 p-0 text-on-surface-variant hover:text-on-surface transition-colors flex items-center justify-center rounded-lg shrink-0 ${className}`}
      >
        <ImageIcon className="h-4 w-4" aria-hidden="true" />
      </Button>

      <ChatHintergrundDialog bereich={bereich} offen={offen} onOffenChange={setzeOffen} />
    </>
  )
}
