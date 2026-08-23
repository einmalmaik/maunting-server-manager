/**
 * Die Bestätigungskarte fürs Aufräumen außerhalb des Sandbox-Ordners.
 *
 * Sie erscheint genau dann, wenn der autonome Modus **aus** ist. Das ist die
 * Regel des Betreibers, und sie gilt ohne Ausnahme: autonomer Modus an, kein
 * Klick; autonomer Modus aus, ein Klick für alles. Entschieden wird das nicht
 * hier und auch nicht in Rust, sondern im Panel (`_desktop_argumente` setzt
 * `autonom`) — die Wahrheit über Rechte liegt im Backend.
 *
 * Warum die Karte auf dem Rechner steht und nicht als Vorschlagskarte im
 * Panel: die Frage "darf das weg?" gehört vor die Augen dessen, dem die
 * Dateien gehören. Und nur der Rechner kann die Liste überhaupt füllen —
 * welcher Pfad wie groß ist und in welcher Zone liegt, weiß das Panel nicht
 * und soll es nicht wissen.
 *
 * Wie die Übernahmekarte meldet sie das Ergebnis des Auftrags selbst: dieser
 * eine Auftrag hat keins, solange er auf einen Menschen wartet.
 */
import { useCallback, useEffect, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { ergebnisMelden } from './desktopJobs'
import {
  aufraeumenAblehnen,
  aufraeumenBestaetigen,
  type Aufraeumplan,
} from './tauri'

export const EREIGNIS_AUFRAEUMEN = 'mss:aufraeumen-anfrage'

/** Wie viele Pfade die Karte zeigt, bevor sie zusammenfasst. */
const SICHTBAR = 12

function groesse(bytes: number | null): string {
  if (bytes === null) {
    return ''
  }
  const einheiten = ['B', 'KB', 'MB', 'GB', 'TB']
  let wert = bytes
  let stelle = 0
  while (wert >= 1024 && stelle < einheiten.length - 1) {
    wert /= 1024
    stelle += 1
  }
  return `${wert.toFixed(wert >= 10 || stelle === 0 ? 0 : 1)} ${einheiten[stelle]}`
}

export function Aufraeumkarte({ offenerAuftragId }: { offenerAuftragId: string | null }) {
  const { t } = useTranslation()
  const [plan, setPlan] = useState<(Aufraeumplan & { auftragId: string }) | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  useEffect(() => {
    const abmelden = listen<Aufraeumplan>(EREIGNIS_AUFRAEUMEN, (ereignis) => {
      if (!offenerAuftragId) {
        return
      }
      setPlan({ ...ereignis.payload, auftragId: offenerAuftragId })
    })
    return () => {
      void abmelden.then((weg) => weg())
    }
  }, [offenerAuftragId])

  const entscheiden = useCallback(
    async (ja: boolean) => {
      if (!plan || laeuft) return
      setLaeuft(true)
      try {
        if (!ja) {
          await aufraeumenAblehnen()
          // Der Hinweis geht als Werkzeugergebnis an das Modell — deshalb
          // bewusst nicht übersetzt: das Modell liest ihn, nicht der Mensch.
          await ergebnisMelden(plan.auftragId, true, {
            bestaetigt: false,
            hinweis:
              'Der Benutzer hat das Aufräumen abgelehnt. Es wurde nichts ' +
              'angefasst. Frag nicht sofort erneut mit derselben Liste.',
          })
          return
        }
        // Bestätigt wird der Plan, den Rust hält — nicht der, den diese Karte
        // anzeigt. Ein manipulierter Renderer könnte sonst eine harmlose
        // Liste zeigen und eine andere löschen lassen.
        const ergebnis = await aufraeumenBestaetigen()
        await ergebnisMelden(plan.auftragId, true, ergebnis)
      } catch (fehler) {
        const text = fehler instanceof Error ? fehler.message : String(fehler)
        await ergebnisMelden(plan.auftragId, false, { fehler: text }, 'DESKTOP_TOOL_FAILED')
      } finally {
        setLaeuft(false)
        setPlan(null)
      }
    },
    [plan, laeuft],
  )

  if (!plan) {
    return null
  }

  const leeren = plan.aktion === 'papierkorb_leeren'
  const hart = plan.aktion === 'endgueltig'
  const summe = plan.posten.reduce((zaehler, posten) => zaehler + (posten.bytes ?? 0), 0)

  return (
    <div className="msm-modal-overlay">
      <div className="msm-card w-full max-w-lg p-6">
        <h2 className="text-lg font-semibold text-on-surface">
          {t(leeren ? 'mss.aufraeumen.frageLeeren' : hart
            ? 'mss.aufraeumen.frageHart'
            : 'mss.aufraeumen.fragePapierkorb')}
        </h2>
        <p className="mt-3 text-sm text-on-surface-variant">{plan.grund}</p>

        {!leeren && (
          <>
            <ul className="mt-4 max-h-56 overflow-y-auto text-xs text-on-surface-variant">
              {plan.posten.slice(0, SICHTBAR).map((posten) => (
                <li
                  key={posten.pfad}
                  className="flex items-baseline justify-between gap-3 border-b border-outline-variant/40 py-1 last:border-0"
                >
                  <span className="truncate font-mono" title={posten.pfad}>
                    {posten.pfad}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {groesse(posten.bytes)}
                    {posten.zone === 'system' ? ` · ${t('mss.aufraeumen.system')}` : ''}
                  </span>
                </li>
              ))}
            </ul>
            {plan.posten.length > SICHTBAR && (
              <p className="mt-2 text-xs text-on-surface-variant">
                {t('mss.aufraeumen.weitere', { anzahl: plan.posten.length - SICHTBAR })}
              </p>
            )}
            <p className="mt-3 text-xs text-on-surface-variant">
              {t('mss.aufraeumen.summe', {
                anzahl: plan.posten.length,
                groesse: groesse(summe),
              })}
            </p>
          </>
        )}

        <p className="mt-3 text-xs text-on-surface-variant">
          {t(hart || leeren ? 'mss.aufraeumen.warnungHart' : 'mss.aufraeumen.warnungWeich')}
        </p>

        <div className="mt-5 flex gap-2">
          <Button disabled={laeuft} onClick={() => void entscheiden(true)}>
            {t(hart || leeren ? 'mss.aufraeumen.jaHart' : 'mss.aufraeumen.jaWeich')}
          </Button>
          <Button variant="secondary" disabled={laeuft} onClick={() => void entscheiden(false)}>
            {t('mss.aufraeumen.nein')}
          </Button>
        </div>
      </div>
    </div>
  )
}
