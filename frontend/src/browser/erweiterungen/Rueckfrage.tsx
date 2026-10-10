/**
 * Vor jeder Installation: was die Erweiterung darf. WebView2 fragt später
 * nicht mehr nach; was hier steht, gilt, sobald sie läuft.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { alleSeiten, BEKANNTE_RECHTE, erweiterungen, fehlerText, type Angaben, type Vorschau } from './erweiterungen'

export function Rechte({ angaben }: { angaben: Angaben }) {
  const { t } = useTranslation()
  const recht = (r: string) => ((BEKANNTE_RECHTE as readonly string[]).includes(r) ? t(`browser.erweiterungen.recht.${r}`) : r)
  const ueberall = angaben.seiten.some(alleSeiten)
  const seiten = angaben.seiten.filter((s) => !alleSeiten(s))
  const leer = !ueberall && seiten.length === 0 && angaben.rechte.length === 0
  return (
    <div className="flex flex-col gap-3 text-body-sm text-on-surface">
      {leer && <p className="text-on-surface-variant">{t('browser.erweiterungen.keineRechte')}</p>}
      {(ueberall || seiten.length > 0) && (
        <div>
          <p className="font-medium">{ueberall ? t('browser.erweiterungen.alleSeiten') : t('browser.erweiterungen.dieseSeiten')}</p>
          {!ueberall && (
            <ul className="mt-1 list-disc pl-5 text-on-surface-variant">
              {seiten.map((s) => (
                <li key={s} className="break-all">
                  {s}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {angaben.rechte.length > 0 && (
        <ul className="list-disc pl-5 text-on-surface-variant">
          {angaben.rechte.map((r) => (
            <li key={r} className="break-words">
              {recht(r)}
            </li>
          ))}
        </ul>
      )}
      {angaben.optional.length > 0 && (
        <p className="text-label-sm text-on-surface-variant">
          {t('browser.erweiterungen.optional', { liste: angaben.optional.map(recht).join(', ') })}
        </p>
      )}
    </div>
  )
}

export function Rueckfrage({ vorschau, fertig }: { vorschau: Vorschau; fertig: () => void }) {
  const { t } = useTranslation()
  const titelId = useId()
  const [laeuft, setLaeuft] = useState(false)
  const { angaben } = vorschau

  const abbrechen = () => {
    void erweiterungen.verwerfen(vorschau.vorgang)
    fertig()
  }
  const installieren = async () => {
    setLaeuft(true)
    try {
      await erweiterungen.installieren(vorschau.vorgang)
      toast.success(t('browser.erweiterungen.installiert', { name: angaben.name }))
    } catch (e) {
      toast.error(fehlerText(t, e))
    }
    fertig()
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && !laeuft && abbrechen()}>
      <DialogContent className="max-w-lg" aria-labelledby={titelId}>
        <DialogHeader className="px-5 py-4 pr-14">
          <div className="flex items-center gap-3">
            {angaben.symbol && <img src={angaben.symbol} alt="" className="h-8 w-8 shrink-0" />}
            <DialogTitle id={titelId} className="text-title-md">
              {t('browser.erweiterungen.frage', { name: angaben.name })}
            </DialogTitle>
          </div>
          <DialogDescription className="text-body-sm">
            {vorschau.herkunft === 'entpackt' ? t('browser.erweiterungen.ungeprueft') : t('browser.erweiterungen.ausDemStore', { version: angaben.version })}
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[50vh] flex-col gap-3 overflow-y-auto px-5">
          <Rechte angaben={angaben} />
          <p className="rounded-md bg-error/10 p-3 text-body-sm text-on-surface">{t('browser.erweiterungen.warnung')}</p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={abbrechen} disabled={laeuft}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => void installieren()} disabled={laeuft} autoFocus>
            {t('browser.erweiterungen.installieren')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
