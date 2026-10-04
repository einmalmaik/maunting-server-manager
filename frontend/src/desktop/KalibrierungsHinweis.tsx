/**
 * Einmalige Frage: das Wake-Word noch auf ein altes Wort kalibriert?
 *
 * Bis 05.10.2026 war das Wake-Word der frei wählbare Name der KI, Standard
 * „Assistent“. Seitdem heißt sie fest Singra — damit trifft der Hinweis fast
 * jeden, der das Wake-Word je eingerichtet hat. Er fragte früher bei jedem
 * Start; jetzt **einmal je altem Wort**. Beide Knöpfe zählen als Antwort, und
 * gemerkt wird das Wort, nicht ein Ja/Nein: wer später auf ein anderes
 * falsches Wort kalibriert, wird wieder gefragt. Die stille Zeile in den
 * Audio-Einstellungen (`WakewordEinrichtung`) bleibt stehen, solange das Wort
 * nicht passt.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { KI_NAME } from '@/lib/kiName'
import { wakewordStand } from './tauri'

export const KALIBRIERUNG_GEFRAGT_KEY = 'mss:kalibrierung_gefragt'

function schonGefragt(wort: string): boolean {
  try {
    return localStorage.getItem(KALIBRIERUNG_GEFRAGT_KEY) === wort
  } catch {
    return false
  }
}

function alsGefragtMerken(wort: string): void {
  try {
    localStorage.setItem(KALIBRIERUNG_GEFRAGT_KEY, wort)
  } catch {
    // Ohne Speicher fragt die App beim nächsten Start eben noch einmal.
  }
}

export function KalibrierungsHinweis() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [altesWort, setAltesWort] = useState<string | null>(null)

  useEffect(() => {
    void wakewordStand()
      .then((stand) => {
        if (stand.trainiert && stand.wort && stand.wort !== KI_NAME && !schonGefragt(stand.wort)) {
          setAltesWort(stand.wort)
        }
      })
      .catch(() => undefined)
  }, [])

  if (altesWort === null) return null

  function beantwortet() {
    if (altesWort !== null) alsGefragtMerken(altesWort)
    setAltesWort(null)
  }

  return (
    <div
      className="msm-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={t('mss.kalibrierung.titel')}
    >
      <div className="msm-card w-full max-w-sm p-5">
        <h2 className="text-sm font-medium text-on-surface">{t('mss.kalibrierung.titel')}</h2>
        <p className="mt-1 text-xs text-on-surface-variant">
          {t('mss.kalibrierung.frage', { neu: KI_NAME, alt: altesWort })}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={beantwortet}>
            {t('mss.kalibrierung.spaeter')}
          </Button>
          <Button
            autoFocus
            onClick={() => {
              beantwortet()
              navigate('/einstellungen?tab=audio')
            }}
          >
            {t('mss.kalibrierung.jetzt')}
          </Button>
        </div>
      </div>
    </div>
  )
}
