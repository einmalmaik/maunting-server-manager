import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge, Button, Switch } from '@/Singra/UI'
import { konfigLaden, konfigSpeichern, type AppKonfig } from '../tauri'

export function ComputerUseSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
  }, [])

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      const neu = { ...konfig, computer_use_aktiv: false }
      setKonfig(neu)
      await konfigSpeichern(neu).catch(() => {})
      onKonfigAenderung?.()
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    const neu = { ...konfig, computer_use_aktiv: true }
    setKonfig(neu)
    setDialogOffen(false)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  return (
    <>
      <div className="flex items-center justify-between gap-3 border-t border-outline-variant/40 pt-4">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm text-on-surface">{t('mss.einstellungen.computerUse.titel')}</p>
            {isAndroid ? (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusNichtVerfuegbar')}
              </Badge>
            ) : konfig?.computer_use_aktiv ? (
              <Badge variant="success">
                {t('mss.einstellungen.computerUse.statusAktiv')}
              </Badge>
            ) : (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusDeaktiviert')}
              </Badge>
            )}
          </div>
          {isAndroid && (
            <p className="text-xs text-on-surface-variant">
              {t('mss.einstellungen.computerUse.androidHinweis')}
            </p>
          )}
        </div>
        <Switch
          checked={!isAndroid && konfig?.computer_use_aktiv === true}
          disabled={isAndroid || konfig === null}
          onCheckedChange={(an) => void toggle(an)}
          aria-label={t('mss.einstellungen.computerUse.titel')}
        />
      </div>

      {dialogOffen && (
        <div
          className="msm-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('mss.einstellungen.computerUse.aktivierenTitel')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.computerUse.aktivierenTitel')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t('mss.einstellungen.computerUse.aktivierenWarnung')}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.computerUse.abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.computerUse.aktivierenBestaetigen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
