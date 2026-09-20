import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Monitor } from 'lucide-react'
import { Button, Checkbox, Dialog, DialogContent, Dropdown } from '@/Singra/UI'
import type { DropdownOption } from '@/components/ui/Dropdown'
import {
  FREIGABE_STANDARD,
  systemtonMoeglich,
  type FreigabeAufloesung,
  type FreigabeBildrate,
  type FreigabeOptionen,
} from '@/services/livekitRaum'

export interface ScreenShareOptionsModalProps {
  isOpen: boolean
  onClose: () => void
  onStart: (optionen: FreigabeOptionen) => void
  initial?: FreigabeOptionen
}

/**
 * Die Einstellungen werden **vor** dem nativen Auswahldialog gefragt.
 *
 * Andersherum ginge es nicht: Auflösung und Bildrate sind Bedingungen an die
 * Aufnahme und müssen feststehen, bevor der Browser die Quelle öffnet.
 */
export const ScreenShareOptionsModal: React.FC<ScreenShareOptionsModalProps> = ({
  isOpen,
  onClose,
  onStart,
  initial = FREIGABE_STANDARD,
}) => {
  const { t } = useTranslation()

  const [aufloesung, setAufloesung] = useState<FreigabeAufloesung>(initial.aufloesung)
  const [bildrate, setBildrate] = useState<FreigabeBildrate>(initial.bildrate)
  const [systemton, setSystemton] = useState(initial.systemton)

  const tonMoeglich = useMemo(() => systemtonMoeglich(), [])

  // Die Beschriftungen stehen in der Sprachdatei, die Werte hier: „720p" ist
  // kein Text, „720p — sparsam" schon.
  const aufloesungen: DropdownOption[] = useMemo(
    () => [
      { value: '720p', label: t('calls.resolution720p') },
      { value: '1080p', label: t('calls.resolution1080p') },
      { value: '1440p', label: t('calls.resolution1440p') },
      { value: 'quelle', label: t('calls.resolutionSource') },
    ],
    [t],
  )
  const bildraten: DropdownOption[] = useMemo(
    () => [
      { value: '30', label: t('calls.fps30') },
      { value: '60', label: t('calls.fps60') },
    ],
    [t],
  )

  const starten = () => {
    onStart({ aufloesung, bildrate, systemton: systemton && tonMoeglich })
    onClose()
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-md p-6 bg-surface-container-high text-on-surface">
        <h3 className="font-headline text-base font-bold text-primary mb-1 flex items-center gap-2">
          <Monitor className="w-4 h-4" />
          {t('calls.shareScreen')}
        </h3>
        <p className="text-xs text-on-surface-variant mb-5">
          {t('calls.shareScreenNote')}
        </p>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-on-surface-variant">{t('calls.resolution')}</span>
            <Dropdown
              options={aufloesungen}
              value={aufloesung}
              onChange={(wert) => setAufloesung(wert as FreigabeAufloesung)}
              className="w-full"
              aria-label={t('calls.resolution')}
            />
          </div>

          <div className="space-y-1.5">
            <span className="text-xs font-medium text-on-surface-variant">{t('calls.frameRate')}</span>
            <Dropdown
              options={bildraten}
              value={String(bildrate)}
              onChange={(wert) => setBildrate(Number(wert) as FreigabeBildrate)}
              className="w-full"
              aria-label={t('calls.frameRate')}
            />
          </div>

          <div className="rounded-xl border border-outline-variant/30 bg-surface-container p-3">
            <label className="flex items-start gap-3 cursor-pointer">
              <Checkbox
                checked={systemton && tonMoeglich}
                onCheckedChange={setSystemton}
                disabled={!tonMoeglich}
                aria-label={t('calls.shareSystemAudio')}
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{t('calls.shareSystemAudio')}</span>
                <span className="block text-xs text-on-surface-variant mt-0.5">
                  {tonMoeglich
                    ? t('calls.systemAudioYes')
                    : t('calls.systemAudioNo')}
                </span>
              </span>
            </label>
          </div>

          {aufloesung === '1440p' && bildrate === 60 && (
            <p className="text-xs text-on-surface-variant leading-relaxed">
              {t('calls.heavySettingsNote')}
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={starten}>{t('calls.chooseSource')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
