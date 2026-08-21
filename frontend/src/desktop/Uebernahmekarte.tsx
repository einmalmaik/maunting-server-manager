/**
 * Die Bestätigungskarte für die Übernahme von Maus und Tastatur.
 *
 * Der einzige Weg zur Freigabe. Die KI kann sie anfragen, aber nicht
 * erteilen: erst ein Klick hier ruft `uebernahme_freigeben`, und die Frist
 * selbst liegt in Rust — nicht im Panel, nicht in dieser Datei.
 *
 * Die Karte meldet auch das Ergebnis des Auftrags zurück, denn genau dieser
 * eine Auftrag hat keins, wenn er ausgeführt wird: er wartet auf einen
 * Menschen. Antwortet niemand, verfällt er panelseitig nach zehn Minuten, und
 * das Modell erfährt das als Verfall statt als Stille.
 */
import { useCallback, useEffect, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { ergebnisMelden } from './desktopJobs'
import { uebernahmeFreigeben, uebernahmeRest, uebernahmeWiderrufen } from './tauri'

interface Anfrage {
  anliegen: string
  minuten: number
  auftragId: string
}

export const EREIGNIS_UEBERNAHME = 'mss:uebernahme-anfrage'

export function Uebernahmekarte({ offenerAuftragId }: { offenerAuftragId: string | null }) {
  const { t } = useTranslation()
  const [anfrage, setAnfrage] = useState<Anfrage | null>(null)
  const [rest, setRest] = useState(0)

  useEffect(() => {
    const abmelden = listen<{ anliegen: string; minuten: number }>(
      EREIGNIS_UEBERNAHME,
      (ereignis) => {
        if (!offenerAuftragId) {
          return
        }
        setAnfrage({
          anliegen: ereignis.payload.anliegen,
          minuten: ereignis.payload.minuten,
          auftragId: offenerAuftragId,
        })
      },
    )
    return () => {
      void abmelden.then((weg) => weg())
    }
  }, [offenerAuftragId])

  // Eine laufende Übernahme muss man sehen. Eine, die man nicht sieht, wäre
  // die schlechteste Fassung dieser Funktion.
  useEffect(() => {
    const takt = setInterval(() => {
      void uebernahmeRest().then(setRest).catch(() => setRest(0))
    }, 1000)
    return () => clearInterval(takt)
  }, [])

  const entscheiden = useCallback(
    async (erteilt: boolean) => {
      if (!anfrage) return
      if (erteilt) {
        await uebernahmeFreigeben(anfrage.minuten)
      }
      // Die Hinweise gehen als Werkzeugergebnis an das Modell — deshalb
      // bewusst nicht übersetzt: das Modell liest sie, nicht der Mensch.
      await ergebnisMelden(anfrage.auftragId, true, {
        freigegeben: erteilt,
        minuten: erteilt ? anfrage.minuten : 0,
        hinweis: erteilt
          ? 'Der Benutzer hat die Übernahme freigegeben. Sie endet nach der genannten Zeit von selbst.'
          : 'Der Benutzer hat die Übernahme abgelehnt. Frag nicht sofort erneut — such einen Weg ohne Maus und Tastatur.',
      })
      setAnfrage(null)
    },
    [anfrage],
  )

  if (anfrage) {
    return (
      <div className="msm-modal-overlay">
        <div className="msm-card w-full max-w-md p-6">
          <h2 className="text-lg font-semibold text-on-surface">
            {t('mss.uebernahme.frage')}
          </h2>
          <p className="mt-3 text-sm text-on-surface-variant">{anfrage.anliegen}</p>
          <p className="mt-3 text-xs text-on-surface-variant">
            {t('mss.uebernahme.erklaerung', { minuten: anfrage.minuten })}
          </p>
          <div className="mt-5 flex gap-2">
            <Button onClick={() => void entscheiden(true)}>
              {t('mss.uebernahme.freigeben')}
            </Button>
            <Button variant="secondary" onClick={() => void entscheiden(false)}>
              {t('mss.uebernahme.ablehnen')}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (rest > 0) {
    return (
      <div className="msm-card fixed bottom-4 right-4 z-30 flex items-center gap-3 px-4 py-2 text-xs">
        <span className="mss-blase inline-block h-2 w-2 rounded-full bg-primary" />
        <span className="text-on-surface">
          {t('mss.uebernahme.aktiv', {
            zeit: `${Math.floor(rest / 60)}:${String(rest % 60).padStart(2, '0')}`,
          })}
        </span>
        <Button variant="secondary" size="sm" onClick={() => void uebernahmeWiderrufen()}>
          {t('mss.uebernahme.beenden')}
        </Button>
      </div>
    )
  }

  return null
}
