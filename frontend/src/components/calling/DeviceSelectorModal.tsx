import React from 'react'
import { Dialog, DialogContent } from '@/Singra/UI'
import { Dropdown, type DropdownOption } from '@/components/ui/Dropdown'
import { Mic, Video, Volume2 } from 'lucide-react'

export interface DeviceSelectorModalProps {
  isOpen: boolean
  onClose: () => void
  audioInputs: DropdownOption[]
  videoInputs: DropdownOption[]
  audioOutputs: DropdownOption[]
  selectedAudioInput: string
  selectedVideoInput: string
  selectedAudioOutput: string
  onSelectAudioInput: (id: string) => void
  onSelectVideoInput: (id: string) => void
  onSelectAudioOutput: (id: string) => void
}

export const DeviceSelectorModal: React.FC<DeviceSelectorModalProps> = ({
  isOpen,
  onClose,
  audioInputs,
  videoInputs,
  audioOutputs,
  selectedAudioInput,
  selectedVideoInput,
  selectedAudioOutput,
  onSelectAudioInput,
  onSelectVideoInput,
  onSelectAudioOutput,
}) => {
  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-md p-6 bg-surface-container-high text-on-surface border border-outline-variant/30 rounded-2xl shadow-2xl">
        <h3 className="font-headline text-base font-bold text-primary mb-4">
          Audio- & Videoeinstellungen
        </h3>
        <div className="space-y-4 py-1">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-on-surface-variant flex items-center gap-1.5">
              <Mic className="w-3.5 h-3.5 text-primary" />
              <span>Mikrofon</span>
            </label>
            <Dropdown
              options={audioInputs}
              value={selectedAudioInput}
              onChange={(val: string | number) => onSelectAudioInput(String(val))}
              className="w-full"
              aria-label="Mikrofon"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-on-surface-variant flex items-center gap-1.5">
              <Video className="w-3.5 h-3.5 text-primary" />
              <span>Kamera</span>
            </label>
            <Dropdown
              options={videoInputs}
              value={selectedVideoInput}
              onChange={(val: string | number) => onSelectVideoInput(String(val))}
              className="w-full"
              aria-label="Kamera"
            />
          </div>

          {audioOutputs.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-on-surface-variant flex items-center gap-1.5">
                <Volume2 className="w-3.5 h-3.5 text-primary" />
                <span>Lautsprecher</span>
              </label>
              <Dropdown
                options={audioOutputs}
                value={selectedAudioOutput}
                onChange={(val: string | number) => onSelectAudioOutput(String(val))}
                className="w-full"
                aria-label="Lautsprecher"
              />
            </div>
          )}
        </div>
        <p className="mt-4 border-t border-outline-variant/30 pt-3 text-xs leading-relaxed text-on-surface-variant">
          Die Wahl gilt auch für Sprachnachrichten und den Sprachmodus. Rauschunterdrückung,
          Echounterdrückung und der Mikrofontest stehen in Profil → Audio.
        </p>
      </DialogContent>
    </Dialog>
  )
}
