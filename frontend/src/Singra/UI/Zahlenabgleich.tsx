import { useId, type ReactNode } from 'react'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'

/**
 * Zahlenabgleich: eine Aktion auf Gerät A wird auf Gerät B bestätigt.
 *
 * A zeigt eine Zahl (`Abgleichzahl`), B bietet mehrere an (`Zahlenwahl`) und
 * man tippt die gleiche. Wer einen Link zugeschickt bekam, ohne selbst etwas
 * angestoßen zu haben, kennt die Zahl nicht. Ob die Wahl stimmt, entscheidet
 * immer der Server; die Bausteine zeigen nur an.
 *
 * Erster Einsatz: Passkey-Bestätigung der App im Browser
 * (`BrowserBestaetigungDialog`, Seite `/bestaetigen`).
 */

export interface AbgleichzahlProps {
  zahl: number
  /** Zeile unter der Zahl, solange auf die andere Seite gewartet wird. */
  warteText: ReactNode
}

/** Die Zahl, die man auf dem anderen Gerät antippen soll, samt Wartezeile. */
export function Abgleichzahl({ zahl, warteText }: AbgleichzahlProps) {
  return (
    <div className="text-center">
      <p className="my-5 font-headline text-5xl font-extrabold tabular-nums text-primary" aria-live="polite">
        {zahl}
      </p>
      <p className="flex items-center justify-center gap-2 text-xs text-on-surface-variant" role="status">
        <Spinner />
        {warteText}
      </p>
    </div>
  )
}

export interface ZahlenwahlProps {
  zahlen: number[]
  onWaehlen: (zahl: number) => void
  /** Beschriftung der Gruppe für Screenreader, z. B. die Aufforderung darüber. */
  label: string
  disabled?: boolean
}

/** Die Auswahl auf dem bestätigenden Gerät: große, gleich breite Zahlenknöpfe. */
export function Zahlenwahl({ zahlen, onWaehlen, label, disabled = false }: ZahlenwahlProps) {
  const id = useId()
  return (
    <div role="group" aria-labelledby={id} className="grid grid-cols-3 gap-3">
      <span id={id} className="sr-only">{label}</span>
      {zahlen.map((zahl) => (
        <Button
          key={zahl}
          type="button"
          variant="secondary"
          size="lg"
          className="h-16 font-headline text-2xl font-extrabold tabular-nums"
          disabled={disabled}
          onClick={() => onWaehlen(zahl)}
        >
          {zahl}
        </Button>
      ))}
    </div>
  )
}
