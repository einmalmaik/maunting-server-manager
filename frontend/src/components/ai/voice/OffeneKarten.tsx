import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiActionProposal } from '@/api/ai'
import { AiActionProposalCard } from '../AiActionProposalCard'

/** Wie oft die Sprachansicht nach wartenden Karten fragt. */
const ABFRAGE_MS = 3000

/**
 * Jede Karte, auf deren Klick gerade jemand wartet — hier, im Sprachmodus.
 *
 * Seit dem 25.09.2026 bestätigt nur noch der Klick, nie ein gesprochenes Ja
 * (Betreiber: „alles wird mit Karte bestätigt, sowohl Chat als auch
 * Echtzeit"). Bis dahin zeigte die Ansicht nur die jüngste eigene Karte, und
 * eine Karte, um die ein Worker bat, erreichte sie nie: man musste den
 * Sprachmodus verlassen und im Worker-Fenster klicken.
 *
 * Quelle ist dieselbe Liste wie im Chat, beschränkt auf das Offene
 * (`listOpenActions`); gezeichnet wird mit derselben Karte, samt Rückfrage vor
 * Unumkehrbarem. Neu geladen wird im Takt und sofort, wenn die Sitzung eine
 * neue Karte meldet (``impuls``).
 */
export function OffeneKarten({
  impuls,
  onAnzahl,
}: {
  impuls: number
  /** Wie viele Karten gerade warten — die Ansicht macht ihnen damit Platz. */
  onAnzahl?: (anzahl: number) => void
}) {
  const { t } = useTranslation()
  const [karten, setKarten] = useState<AiActionProposal[]>([])
  // Entschiedene Karten. Eine Abfrage, die vor dem Klick losging, brächte
  // sie sonst noch einmal als offen zurück — mit einem Knopf, der nur noch
  // einen Fehler auslöst.
  const entschieden = useRef(new Set<string>())

  const laden = useCallback(async () => {
    try {
      const liste = await aiApi.listOpenActions()
      setKarten(
        liste.filter(
          (karte) =>
            karte.status === 'proposed' &&
            !karte.autonomous &&
            !entschieden.current.has(karte.id),
        ),
      )
    } catch {
      // Der nächste Takt fragt wieder; eine Störung hier ist keine Meldung wert.
    }
  }, [])

  useEffect(() => {
    void laden()
  }, [laden, impuls])

  useEffect(() => {
    const takt = window.setInterval(() => void laden(), ABFRAGE_MS)
    return () => window.clearInterval(takt)
  }, [laden])

  useEffect(() => {
    onAnzahl?.(karten.length)
  }, [karten.length, onAnzahl])

  const geaendert = useCallback((neu: AiActionProposal) => {
    if (neu.status === 'proposed') {
      setKarten((bisher) => bisher.map((karte) => (karte.id === neu.id ? neu : karte)))
      return
    }
    entschieden.current.add(neu.id)
    setKarten((bisher) => bisher.filter((karte) => karte.id !== neu.id))
  }, [])

  if (karten.length === 0) return null
  return (
    <section
      className="mt-4 flex max-h-[45vh] w-full max-w-2xl shrink-0 flex-col gap-2 overflow-y-auto"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />
        <h3 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.voice.vorschlag.heading')}
        </h3>
      </div>
      <p className="text-xs text-on-surface-variant/70">{t('ai.voice.vorschlag.hint')}</p>
      {karten.map((karte) => (
        <AiActionProposalCard key={karte.id} proposal={karte} onChange={geaendert} />
      ))}
    </section>
  )
}
