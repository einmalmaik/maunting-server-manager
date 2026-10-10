/**
 * Was ein Screenshot gerade zeigt: die Auswahl über der Seite oder den
 * Ergebnis-Dialog mit Kopieren und Speichern. Steht in der Seitenfläche.
 */
import { useEffect, useId } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { nativ } from '../services/nativ'
import { useAufnahme } from './ablauf'
import { Auswahl } from './Auswahl'
import { kopieren, kopierenMoeglich } from './bild'

export function Aufnahmen() {
  const { t } = useTranslation()
  const stand = useAufnahme((s) => s.stand)
  const schliessen = useAufnahme((s) => s.schliessen)

  useEffect(() => {
    if (stand?.art !== 'fehler') return
    toast.error(t('browser.aufnahme.fehler'))
    schliessen()
  }, [stand, schliessen, t])

  if (stand?.art === 'auswahl') return <Auswahl key={stand.adresse} adresse={stand.adresse} />
  if (stand?.art === 'ergebnis') return <Ergebnis key={stand.adresse} bild={stand.bild} adresse={stand.adresse} abgeschnitten={stand.abgeschnitten} />
  return null
}

function Ergebnis({ bild, adresse, abgeschnitten }: { bild: Blob; adresse: string; abgeschnitten: boolean }) {
  const { t } = useTranslation()
  const titelId = useId()
  const { speichern, schliessen } = useAufnahme.getState()
  const kopierbar = kopierenMoeglich()

  const inZwischenablage = async () => {
    try {
      await kopieren(bild)
      toast.success(t('browser.aufnahme.kopiert'))
      schliessen()
    } catch {
      toast.error(t('browser.aufnahme.kopierFehler'))
    }
  }
  const alsDatei = async () => {
    try {
      const datei = await speichern()
      const name = datei.split(/[\\/]/).pop() ?? datei
      toast.success(t('browser.aufnahme.gespeichert', { name }), { label: t('browser.aufnahme.zeigen'), ausfuehren: () => void nativ.downloadZeigen(datei) })
      schliessen()
    } catch {
      toast.error(t('browser.aufnahme.speicherFehler'))
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && schliessen()}>
      <DialogContent className="max-w-3xl" aria-labelledby={titelId}>
        <DialogHeader className="px-5 py-4 pr-14">
          <DialogTitle id={titelId} className="text-title-md">
            {t('browser.aufnahme.titel')}
          </DialogTitle>
          {abgeschnitten && <DialogDescription className="text-body-sm">{t('browser.aufnahme.abgeschnitten')}</DialogDescription>}
        </DialogHeader>
        <div className="px-5">
          <img src={adresse} alt={t('browser.aufnahme.vorschau')} className="max-h-[60vh] w-full rounded-md bg-surface-container object-contain" />
        </div>
        <DialogFooter>
          <Button variant={kopierbar ? 'ghost' : 'primary'} onClick={() => void alsDatei()} autoFocus={!kopierbar}>
            {t('browser.aufnahme.speichern')}
          </Button>
          {kopierbar && (
            <Button onClick={() => void inZwischenablage()} autoFocus>
              {t('browser.aufnahme.kopieren')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
