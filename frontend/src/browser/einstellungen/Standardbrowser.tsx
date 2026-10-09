/**
 * Unter Android: ist der Browser der Standardbrowser, öffnen Links aus
 * anderen Apps (WhatsApp, Mail) hier. Festlegen fragt Android selbst; nach
 * zweimal „Nein“ fragt es nicht mehr, dann bleiben die Einstellungen.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { nativ } from '../services/nativ'
import { Aktionszeile } from './bausteine'

export function Standardbrowser() {
  const { t } = useTranslation()
  const [standard, setStandard] = useState<boolean | null>(null)
  const [gefragt, setGefragt] = useState(false)

  const lesen = useCallback(() => {
    nativ
      .standardbrowser()
      .then(setStandard)
      .catch(() => setStandard(null))
  }, [])

  useEffect(() => {
    lesen()
    // Zurück aus den Android-Einstellungen: der Stand kann sich geändert haben.
    const sichtbar = () => {
      if (document.visibilityState === 'visible') lesen()
    }
    document.addEventListener('visibilitychange', sichtbar)
    return () => document.removeEventListener('visibilitychange', sichtbar)
  }, [lesen])

  const festlegen = () => {
    nativ
      .standardbrowserWerden()
      .then((ja) => {
        setStandard(ja)
        setGefragt(true)
      })
      .catch(() => toast.error(t('browser.einstellungen.standard.fehler')))
  }

  const einstellungen = () => {
    nativ
      .standardbrowserEinstellungen()
      .then(setStandard)
      .catch(() => toast.error(t('browser.einstellungen.standard.fehler')))
  }

  if (standard === null) return null

  return (
    <Aktionszeile
      name={t('browser.einstellungen.standard.name')}
      hinweis={
        standard
          ? t('browser.einstellungen.standard.ist')
          : gefragt
            ? t('browser.einstellungen.standard.nichtGefragt')
            : t('browser.einstellungen.standard.nicht')
      }
    >
      {!standard && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={festlegen}>
            {t('browser.einstellungen.standard.festlegen')}
          </Button>
          {gefragt && (
            <Button type="button" size="sm" variant="ghost" onClick={einstellungen}>
              {t('browser.einstellungen.standard.einstellungen')}
            </Button>
          )}
        </div>
      )}
    </Aktionszeile>
  )
}
