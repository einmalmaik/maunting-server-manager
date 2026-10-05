import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge, Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { konfigAendern, konfigLaden, type AppKonfig } from '../tauri'

export function ComputerUseSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
  }, [])

  // Nur das eigene Feld, frisch in Rust gemischt (`konfigAendern`). Angezeigt
  // wird, was gespeichert ist — schlägt das Speichern fehl, bleibt der alte
  // Stand stehen, statt „Aktiv“ vorzutäuschen.
  async function setzen(an: boolean) {
    try {
      setKonfig(await konfigAendern({ computer_use_aktiv: an }))
      onKonfigAenderung?.()
    } catch {
      toast.error(t('mss.einstellungen.speichernFehler'))
    }
  }

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      await setzen(false)
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    setDialogOffen(false)
    await setzen(true)
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
