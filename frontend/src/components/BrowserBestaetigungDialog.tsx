import { useTranslation } from 'react-i18next'
import { Globe } from 'lucide-react'
import { Button, Dialog, DialogContent } from '@/Singra/UI'
import { Spinner } from '@/components/ui/Spinner'
import { oeffneBrowser } from '@/desktop/tauri'
import { useBrowserBestaetigung } from '@/stores/browserBestaetigung'

/**
 * Wartet auf die Passkey-Bestätigung im Browser (nur App, einmal in
 * `DesktopApp` eingehängt). Die Zahl tippt man im Browser an; wer den Link
 * nicht selbst geöffnet hat, kennt sie nicht.
 */
export function BrowserBestaetigungDialog() {
  const { t } = useTranslation()
  const offen = useBrowserBestaetigung((s) => s.offen)

  return (
    <Dialog open={offen !== null} onOpenChange={(auf) => { if (!auf) offen?.abbrechen() }}>
      {offen && (
        <DialogContent className="max-w-sm p-6 text-center" aria-labelledby="browser-bestaetigung-titel">
          <Globe className="mx-auto mb-3 h-8 w-8 text-primary" aria-hidden="true" />
          <h2 id="browser-bestaetigung-titel" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('auth.browserBestaetigung.appTitle')}
          </h2>
          <p className="mt-2 text-sm text-on-surface-variant">{t('auth.browserBestaetigung.appText')}</p>
          <p className="my-5 font-headline text-5xl font-extrabold tabular-nums text-primary" aria-live="polite">
            {offen.zahl}
          </p>
          <p className="flex items-center justify-center gap-2 text-xs text-on-surface-variant" role="status">
            <Spinner />
            {t('auth.browserBestaetigung.appWaiting')}
          </p>
          <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
            <Button variant="secondary" type="button" onClick={() => void oeffneBrowser(offen.adresse)}>
              {t('auth.browserBestaetigung.reopen')}
            </Button>
            <Button variant="ghost" type="button" onClick={offen.abbrechen}>
              {t('common.cancel')}
            </Button>
          </div>
        </DialogContent>
      )}
    </Dialog>
  )
}
