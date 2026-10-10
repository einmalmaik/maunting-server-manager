/**
 * Die Titelleiste des Hauptfensters (Windows, macOS, Linux). Das Fenster hat
 * keinen Rahmen des Systems (`decorations: false`); gezogen wird an der
 * freien Fläche, ein Doppelklick maximiert. Das X geht denselben Weg wie das
 * des Systems: Rust hält das Fenster an und `SchliessenDialog` fragt.
 *
 * Unter Android gibt es kein Fenster und keine Leiste.
 */
import { getCurrentWindow } from '@tauri-apps/api/window'
import { Minus, Square, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'

type Aktion = 'minimieren' | 'maximieren' | 'schliessen'

function ausfuehren(aktion: Aktion) {
  const fenster = getCurrentWindow()
  const lauf = aktion === 'minimieren' ? fenster.minimize() : aktion === 'maximieren' ? fenster.toggleMaximize() : fenster.close()
  void lauf.catch(() => undefined)
}

export function Titelleiste() {
  const { t } = useTranslation()
  if (/android/i.test(navigator.userAgent)) return null

  const knopf = 'flex w-11 items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
  return (
    <div data-tauri-drag-region className="relative z-20 flex h-8 shrink-0 select-none items-stretch justify-end bg-surface-container-low">
      {(
        [
          ['minimieren', Minus, t('mss.fenster.minimieren')],
          ['maximieren', Square, t('mss.fenster.maximieren')],
        ] as const
      ).map(([aktion, Symbol, name]) => (
        <Kurzinfo key={aktion} text={name} seite="ende" aussen="h-full">
          <button type="button" onClick={() => ausfuehren(aktion)} className={knopf} aria-label={name}>
            <Symbol className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </Kurzinfo>
      ))}
      <Kurzinfo text={t('mss.fenster.schliessen')} seite="ende" aussen="h-full">
        <button
          type="button"
          onClick={() => ausfuehren('schliessen')}
          className="flex w-11 items-center justify-center text-on-surface-variant hover:bg-status-destructive hover:text-white"
          aria-label={t('mss.fenster.schliessen')}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </Kurzinfo>
    </div>
  )
}
