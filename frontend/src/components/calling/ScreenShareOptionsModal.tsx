import React, { useMemo, useState } from 'react'
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

const AUFLOESUNGEN: DropdownOption[] = [
  { value: '720p', label: '720p — sparsam' },
  { value: '1080p', label: '1080p — Full HD' },
  { value: '1440p', label: '1440p — 2K' },
  { value: 'quelle', label: 'Quelle — so groß wie der Bildschirm' },
]

const BILDRATEN: DropdownOption[] = [
  { value: '30', label: '30 FPS — Text und Dokumente' },
  { value: '60', label: '60 FPS — Spiele und Video' },
]

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
  const [aufloesung, setAufloesung] = useState<FreigabeAufloesung>(initial.aufloesung)
  const [bildrate, setBildrate] = useState<FreigabeBildrate>(initial.bildrate)
  const [systemton, setSystemton] = useState(initial.systemton)

  const tonMoeglich = useMemo(() => systemtonMoeglich(), [])

  const starten = () => {
    onStart({ aufloesung, bildrate, systemton: systemton && tonMoeglich })
    onClose()
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-md p-6 bg-surface-container-high text-on-surface">
        <h3 className="font-headline text-base font-bold text-primary mb-1 flex items-center gap-2">
          <Monitor className="w-4 h-4" />
          Bildschirm teilen
        </h3>
        <p className="text-xs text-on-surface-variant mb-5">
          Danach fragt der Browser, welchen Bildschirm oder welches Fenster du teilen möchtest.
        </p>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-on-surface-variant">Auflösung</span>
            <Dropdown
              options={AUFLOESUNGEN}
              value={aufloesung}
              onChange={(wert) => setAufloesung(wert as FreigabeAufloesung)}
              className="w-full"
              aria-label="Auflösung"
            />
          </div>

          <div className="space-y-1.5">
            <span className="text-xs font-medium text-on-surface-variant">Bildrate</span>
            <Dropdown
              options={BILDRATEN}
              value={String(bildrate)}
              onChange={(wert) => setBildrate(Number(wert) as FreigabeBildrate)}
              className="w-full"
              aria-label="Bildrate"
            />
          </div>

          <div className="rounded-xl border border-outline-variant/30 bg-surface-container p-3">
            <label className="flex items-start gap-3 cursor-pointer">
              <Checkbox
                checked={systemton && tonMoeglich}
                onCheckedChange={setSystemton}
                disabled={!tonMoeglich}
                aria-label="Spiel- und Systemton mitübertragen"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">Spiel- und Systemton mitübertragen</span>
                <span className="block text-xs text-on-surface-variant mt-0.5">
                  {tonMoeglich
                    ? 'Die anderen hören, was auf deinem Rechner läuft. Im Auswahldialog musst du den Ton zusätzlich freigeben.'
                    : 'Dieser Browser kann keinen Systemton übertragen. Chrome und Edge können es.'}
                </span>
              </span>
            </label>
          </div>

          {aufloesung === '1440p' && bildrate === 60 && (
            <p className="text-xs text-on-surface-variant leading-relaxed">
              2K mit 60 Bildern braucht eine gute Leitung in beide Richtungen. Ruckelt es beim
              Gegenüber, sind 1080p oder 30 Bilder meist die bessere Wahl.
            </p>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={starten}>Quelle auswählen</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
