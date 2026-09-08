import React, { useState, useRef } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Button,
} from '@/Singra/UI'
import {
  Sparkles,
  Upload,
  Check,
  RotateCcw,
  Sliders,
  Image as ImageIcon,
  Compass,
  Layers,
} from 'lucide-react'
import {
  type WallpaperPresetId,
  type ChatWallpaperConfig,
  DEFAULT_WALLPAPER_CONFIG,
  saveChatWallpaperConfig,
} from './ChatWallpaper'

interface ChatWallpaperModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentConfig: ChatWallpaperConfig
  onSaveConfig: (config: ChatWallpaperConfig) => void
}

export function ChatWallpaperModal({
  open,
  onOpenChange,
  currentConfig,
  onSaveConfig,
}: ChatWallpaperModalProps) {
  const [selectedPreset, setSelectedPreset] = useState<WallpaperPresetId>(currentConfig.preset)
  const [customDataUrl, setCustomDataUrl] = useState<string | undefined>(currentConfig.customDataUrl)
  const [dimLevel, setDimLevel] = useState<number>(currentConfig.dimLevel)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUploadError(null)
    const file = e.target.files?.[0]
    if (!file) return

    if (!file.type.startsWith('image/')) {
      setUploadError('Bitte wähle eine gültige Bilddatei (JPEG, PNG, WebP) aus.')
      return
    }

    if (file.size > 8 * 1024 * 1024) {
      setUploadError('Das Bild ist zu groß (maximal 8 MB erlaubt).')
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      setCustomDataUrl(dataUrl)
      setSelectedPreset('custom')
    }
    reader.onerror = () => {
      setUploadError('Bild konnte nicht geladen werden.')
    }
    reader.readAsDataURL(file)
  }

  const handleApply = () => {
    const newConfig: ChatWallpaperConfig = {
      preset: selectedPreset,
      customDataUrl: selectedPreset === 'custom' ? customDataUrl : undefined,
      dimLevel,
    }
    saveChatWallpaperConfig(newConfig)
    onSaveConfig(newConfig)
    onOpenChange(false)
  }

  const handleResetDefault = () => {
    setSelectedPreset(DEFAULT_WALLPAPER_CONFIG.preset)
    setCustomDataUrl(undefined)
    setDimLevel(DEFAULT_WALLPAPER_CONFIG.dimLevel)
    saveChatWallpaperConfig(DEFAULT_WALLPAPER_CONFIG)
    onSaveConfig(DEFAULT_WALLPAPER_CONFIG)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md w-full bg-surface-container border border-outline-variant/40 shadow-xl rounded-2xl p-5 space-y-4">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base font-bold text-on-surface">
            <ImageIcon className="w-5 h-5 text-primary" />
            <span>Chat-Hintergrund anpassen</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-on-surface-variant">
            Wähle einen atmosphärischen Hintergrund für deine Unterhaltungen. Das Cyber-Grid-Raster ist standardmäßig aktiv und sorgt für optimalen Kontrast.
          </DialogDescription>
        </DialogHeader>

        {/* Wallpaper Presets Selection */}
        <div className="space-y-3">
          <label className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-primary" />
            <span>Design-Hintergründe</span>
          </label>

          <div className="grid grid-cols-2 gap-2.5">
            {/* 1. Cyber Matrix Grid (Standard) */}
            <button
              type="button"
              onClick={() => setSelectedPreset('cyber')}
              className={`group relative p-3 rounded-xl border text-left flex flex-col justify-between h-24 overflow-hidden transition-all ${
                selectedPreset === 'cyber'
                  ? 'border-primary ring-2 ring-primary/40 bg-primary/10'
                  : 'border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60'
              }`}
            >
              <div
                className="absolute inset-0 opacity-20 pointer-events-none"
                style={{
                  backgroundImage: 'radial-gradient(#06b6d4 1.2px, transparent 1.2px)',
                  backgroundSize: '16px 16px',
                }}
              />
              <div className="relative z-10 flex items-center justify-between w-full">
                <div className="p-1 rounded-md bg-cyan-500/20 text-cyan-400">
                  <Layers className="w-3.5 h-3.5" />
                </div>
                {selectedPreset === 'cyber' && (
                  <div className="w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center">
                    <Check className="w-2.5 h-2.5 stroke-[3]" />
                  </div>
                )}
              </div>
              <div className="relative z-10">
                <p className="text-xs font-bold text-on-surface flex items-center gap-1">
                  <span>Cyber Grid</span>
                  <span className="text-[9px] px-1 py-0.2 rounded bg-primary/20 text-primary font-mono">Standard</span>
                </p>
                <p className="text-[10px] text-on-surface-variant/80">Subtiles Daten-Raster</p>
              </div>
            </button>

            {/* 2. Deep Petrol (MSM Gradient) */}
            <button
              type="button"
              onClick={() => setSelectedPreset('petrol')}
              className={`group relative p-3 rounded-xl border text-left flex flex-col justify-between h-24 overflow-hidden transition-all ${
                selectedPreset === 'petrol'
                  ? 'border-primary ring-2 ring-primary/40 bg-primary/10'
                  : 'border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60'
              }`}
            >
              <div className="absolute inset-0 bg-gradient-to-br from-[#06181d] via-[#092228] to-[#040e11] pointer-events-none" />
              <div className="relative z-10 flex items-center justify-between w-full">
                <div className="p-1 rounded-md bg-teal-500/20 text-teal-300">
                  <Compass className="w-3.5 h-3.5" />
                </div>
                {selectedPreset === 'petrol' && (
                  <div className="w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center">
                    <Check className="w-2.5 h-2.5 stroke-[3]" />
                  </div>
                )}
              </div>
              <div className="relative z-10">
                <p className="text-xs font-bold text-on-surface">Deep Petrol</p>
                <p className="text-[10px] text-on-surface-variant/80">Singra Brand Gradient</p>
              </div>
            </button>

            {/* 3. Midnight Deep Space */}
            <button
              type="button"
              onClick={() => setSelectedPreset('midnight')}
              className={`group relative p-3 rounded-xl border text-left flex flex-col justify-between h-24 overflow-hidden transition-all ${
                selectedPreset === 'midnight'
                  ? 'border-primary ring-2 ring-primary/40 bg-primary/10'
                  : 'border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60'
              }`}
            >
              <div className="absolute inset-0 bg-gradient-to-br from-[#0c1322] via-[#090e1a] to-[#040810] pointer-events-none" />
              <div className="relative z-10 flex items-center justify-between w-full">
                <div className="p-1 rounded-md bg-indigo-500/20 text-indigo-400">
                  <Sparkles className="w-3.5 h-3.5" />
                </div>
                {selectedPreset === 'midnight' && (
                  <div className="w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center">
                    <Check className="w-2.5 h-2.5 stroke-[3]" />
                  </div>
                )}
              </div>
              <div className="relative z-10">
                <p className="text-xs font-bold text-on-surface">Mitternacht</p>
                <p className="text-[10px] text-on-surface-variant/80">Tiefblaues Weltall</p>
              </div>
            </button>

            {/* 4. Minimalist Plain */}
            <button
              type="button"
              onClick={() => setSelectedPreset('minimal')}
              className={`group relative p-3 rounded-xl border text-left flex flex-col justify-between h-24 overflow-hidden transition-all ${
                selectedPreset === 'minimal'
                  ? 'border-primary ring-2 ring-primary/40 bg-primary/10'
                  : 'border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60'
              }`}
            >
              <div className="absolute inset-0 bg-surface-container-lowest pointer-events-none" />
              <div className="relative z-10 flex items-center justify-between w-full">
                <div className="p-1 rounded-md bg-surface-container-highest text-on-surface-variant">
                  <RotateCcw className="w-3.5 h-3.5" />
                </div>
                {selectedPreset === 'minimal' && (
                  <div className="w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center">
                    <Check className="w-2.5 h-2.5 stroke-[3]" />
                  </div>
                )}
              </div>
              <div className="relative z-10">
                <p className="text-xs font-bold text-on-surface">Schlicht Dunkel</p>
                <p className="text-[10px] text-on-surface-variant/80">Ohne Musterung</p>
              </div>
            </button>
          </div>
        </div>

        {/* Custom Image Upload Section */}
        <div className="space-y-2 pt-1 border-t border-outline-variant/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
              <Upload className="w-3.5 h-3.5 text-primary" />
              <span>Eigenes Hintergrundbild</span>
            </span>
            {customDataUrl && (
              <span className="text-[10px] text-emerald-400 font-medium flex items-center gap-1">
                <Check className="w-3 h-3" />
                <span>Bild hinterlegt</span>
              </span>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={handleFileUpload}
            className="hidden"
          />

          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant={selectedPreset === 'custom' ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              className="text-xs gap-1.5 h-8 flex-1"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>{customDataUrl ? 'Anderes Bild wählen' : 'Bild von Gerät hochladen'}</span>
            </Button>

            {customDataUrl && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setSelectedPreset('custom')}
                className={`text-xs h-8 px-2.5 ${selectedPreset === 'custom' ? 'text-primary font-bold' : 'text-on-surface-variant'}`}
              >
                Aktivieren
              </Button>
            )}
          </div>

          {customDataUrl && (
            <div className="relative h-16 w-full rounded-xl overflow-hidden border border-outline-variant/40 mt-1.5">
              <img
                src={customDataUrl}
                alt="Vorschau Hintergrund"
                className="w-full h-full object-cover"
              />
              <div className="absolute inset-0 bg-black/25 flex items-center justify-center">
                <span className="text-[11px] font-medium text-white/90 bg-black/50 px-2 py-0.5 rounded-full">
                  Verzerrungsfrei eingepasst (Cover)
                </span>
              </div>
            </div>
          )}

          {uploadError && (
            <p className="text-[11px] text-error">{uploadError}</p>
          )}
        </div>

        {/* Dimming / Contrast Slider */}
        <div className="space-y-1.5 pt-1 border-t border-outline-variant/20">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-on-surface flex items-center gap-1.5">
              <Sliders className="w-3.5 h-3.5 text-primary" />
              <span>Hintergrund-Abdunklung (Kontrast)</span>
            </span>
            <span className="font-mono text-[11px] text-primary">{dimLevel}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="75"
            step="5"
            value={dimLevel}
            onChange={(e) => setDimLevel(Number(e.target.value))}
            className="w-full accent-primary h-1.5 bg-surface-container-highest rounded-lg cursor-pointer"
          />
          <div className="flex justify-between text-[10px] text-on-surface-variant/60">
            <span>Hell / Natürlich</span>
            <span>Optimal lesbar</span>
            <span>Sehr dunkel</span>
          </div>
        </div>

        <DialogFooter className="flex items-center justify-between pt-3 border-t border-outline-variant/20 bg-transparent">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleResetDefault}
            className="text-xs h-8 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high gap-1 px-2.5"
            title="Auf Standard 'Cyber Grid' zurücksetzen"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Zurücksetzen</span>
          </Button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="inline-flex items-center justify-center h-8 px-3.5 text-xs font-medium rounded-lg border border-outline-variant/50 bg-surface-container-high hover:bg-surface-container-highest text-on-surface hover:border-outline transition-all active:scale-[0.98]"
            >
              Abbrechen
            </button>
            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={handleApply}
              className="text-xs h-8 px-4 font-semibold"
            >
              Übernehmen
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
