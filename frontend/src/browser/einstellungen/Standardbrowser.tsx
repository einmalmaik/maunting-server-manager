/**
 * Ist der Browser der Standardbrowser, öffnen Links aus anderen Apps und
 * Programmen hier. Unter Android fragt Festlegen Android selbst; nach
 * zweimal „Nein“ fragt es nicht mehr, dann bleiben die Einstellungen. Unter
 * Windows legt sich keine App selbst fest: Festlegen öffnet die Standard-Apps
 * des Browsers in den Windows-Einstellungen.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { Aktionszeile } from './bausteine'

export function Standardbrowser() {
  const { t } = useTranslation()
  const [standard, setStandard] = useState<boolean | null>(null)
  const [gefragt, setGefragt] = useState(false)
  const android = istAndroid()

  const lesen = useCallback(() => {
    nativ
      .standardbrowser()
      .then(setStandard)
      .catch(() => setStandard(null))
  }, [])

  useEffect(() => {
    lesen()
    // Zurück aus den Einstellungen des Systems: der Stand kann sich geändert
    // haben. Android meldet das per Sichtbarkeit, Windows per Fokus.
    const sichtbar = () => {
      if (document.visibilityState === 'visible') lesen()
    }
    document.addEventListener('visibilitychange', sichtbar)
    window.addEventListener('focus', lesen)
    return () => {
      document.removeEventListener('visibilitychange', sichtbar)
      window.removeEventListener('focus', lesen)
    }
  }, [lesen])

  const fehler = () => toast.error(t(android ? 'browser.einstellungen.standard.fehler' : 'browser.einstellungen.standard.fehlerWindows'))

  const festlegen = () => {
    nativ
      .standardbrowserWerden()
      .then((ja) => {
        setStandard(ja)
        setGefragt(true)
      })
      .catch(fehler)
  }

  const einstellungen = () => {
    nativ.standardbrowserEinstellungen().then(setStandard).catch(fehler)
  }

  if (standard === null) return null

  const hinweis = android
    ? standard
      ? t('browser.einstellungen.standard.ist')
      : gefragt
        ? t('browser.einstellungen.standard.nichtGefragt')
        : t('browser.einstellungen.standard.nicht')
    : standard
      ? t('browser.einstellungen.standard.istWindows')
      : gefragt
        ? t('browser.einstellungen.standard.gefragtWindows')
        : t('browser.einstellungen.standard.nichtWindows')

  return (
    <Aktionszeile name={t('browser.einstellungen.standard.name')} hinweis={hinweis}>
      {!standard && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={festlegen}>
            {t(android ? 'browser.einstellungen.standard.festlegen' : 'browser.einstellungen.standard.festlegenWindows')}
          </Button>
          {android && gefragt && (
            <Button type="button" size="sm" variant="ghost" onClick={einstellungen}>
              {t('browser.einstellungen.standard.einstellungen')}
            </Button>
          )}
        </div>
      )}
    </Aktionszeile>
  )
}
