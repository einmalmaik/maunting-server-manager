import { useState, useRef } from 'react'
import {
  Image,
  Upload,
  RotateCcw,
  Sliders,
  Check,
  X,
  Sparkles,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'

export const WALLPAPER_PRESETS = [
  {
    id: 'none',
    name: 'Standard Dunkel',
    css: '',
    preview: 'bg-background',
  },
  {
    id: 'cyber-deep',
    name: 'Cyber Deep',
    css: 'linear-gradient(135deg, #090a1a 0%, #17153b 50%, #0c0d1b 100%)',
    preview: 'bg-gradient-to-br from-background via-primary/20 to-background',
  },
  {
    id: 'emerald-glow',
    name: 'Emerald Glow',
    css: 'linear-gradient(135deg, #021a14 0%, #063c2c 50%, #03120e 100%)',
    preview: 'bg-gradient-to-br from-background via-status-success/20 to-background',
  },
  {
    id: 'cosmic-void',
    name: 'Cosmic Void',
    css: 'linear-gradient(135deg, #050b14 0%, #0d2137 50%, #030712 100%)',
    preview: 'bg-gradient-to-br from-background via-muted to-background',
  },
  {
    id: 'obsidian-mesh',
    name: 'Obsidian Mesh',
    css: 'radial-gradient(circle at 50% 50%, #1c1d24 0%, #0b0c10 100%)',
    preview: 'bg-muted',
  },
]

interface WallpaperManagerProps {
  onClose?: () => void
  isModal?: boolean
}

export function WallpaperManager({ onClose, isModal = false }: WallpaperManagerProps) {
  const {
    customWallpaper,
    wallpaperBlur,
    wallpaperDim,
    setCustomWallpaper,
    setWallpaperBlur,
    setWallpaperDim,
  } = useBrowserStore()

  const [urlInput, setUrlInput] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setCustomWallpaper(reader.result)
      }
    }
    reader.readAsDataURL(file)
  }

  const handleApplyUrl = (e: React.FormEvent) => {
    e.preventDefault()
    if (!urlInput.trim()) return
    setCustomWallpaper(urlInput.trim())
    setUrlInput('')
  }

  const handleReset = () => {
    setCustomWallpaper(null)
    setWallpaperBlur(0)
    setWallpaperDim(40)
  }

  const content = (
    <div className="space-y-4 select-none">
      {/* Kopfzeile bei Modal */}
      {isModal && (
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <Image className="w-4 h-4 text-primary" />
            <h3 className="font-semibold text-sm text-foreground">Startseite anpassen</h3>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              title="Schließen"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      )}

      {/* Vordefinierte Hintergründe */}
      <div>
        <label className="text-xs font-semibold text-foreground flex items-center gap-1.5 mb-2">
          <Sparkles className="w-3.5 h-3.5 text-primary" />
          Themen & Farbverläufe
        </label>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {WALLPAPER_PRESETS.map((preset) => {
            const isSelected = (!customWallpaper && preset.id === 'none') || customWallpaper === preset.css

            return (
              <button
                key={preset.id}
                type="button"
                onClick={() => setCustomWallpaper(preset.css || null)}
                className={`relative p-2.5 rounded-xl border text-left transition-all ${
                  isSelected
                    ? 'border-primary ring-2 ring-primary/20 shadow-sm'
                    : 'border-border hover:border-primary/50 bg-card'
                }`}
              >
                <div
                  className={`w-full h-12 rounded-lg mb-1.5 border border-border/50 flex items-center justify-center ${preset.preview}`}
                  style={{ background: preset.css || undefined }}
                >
                  {isSelected && <Check className="w-4 h-4 text-primary" />}
                </div>
                <div className="text-label-sm font-medium text-foreground truncate">{preset.name}</div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Eigenes Bild hochladen oder URL */}
      <div className="pt-2 border-t border-border space-y-2.5">
        <label className="text-xs font-semibold text-foreground flex items-center gap-1.5">
          <Upload className="w-3.5 h-3.5 text-primary" />
          Eigenes Hintergrundbild
        </label>

        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={handleFileUpload}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-card border border-border hover:bg-muted hover:border-primary/50 text-foreground text-xs font-medium transition-colors"
          >
            <Upload className="w-3.5 h-3.5 text-muted-foreground" />
            <span>Datei auswählen</span>
          </button>

          {customWallpaper && (
            <button
              type="button"
              onClick={handleReset}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-muted hover:bg-muted/80 text-muted-foreground hover:text-foreground text-xs font-medium transition-colors"
              title="Auf Standard zurücksetzen"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Standard</span>
            </button>
          )}
        </div>

        {/* Bild-URL */}
        <form onSubmit={handleApplyUrl} className="flex gap-2">
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="Oder Bild-URL einfügen (https://...)"
            className="flex-1 bg-card border border-border rounded-xl px-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground outline-none focus:border-primary"
          />
          <button
            type="submit"
            className="px-3 py-1.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 text-xs font-medium transition-colors shrink-0"
          >
            Laden
          </button>
        </form>
      </div>

      {/* Regler für Abdunkelung und Weichzeichner */}
      {customWallpaper && (
        <div className="pt-2 border-t border-border space-y-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            <Sliders className="w-3.5 h-3.5 text-primary" />
            <span>Darstellungs-Filter</span>
          </div>

          {/* Dimmer */}
          <div className="space-y-1">
            <div className="flex justify-between text-label-sm text-muted-foreground">
              <span>Abdunkelung (Kontrast)</span>
              <span>{wallpaperDim}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="90"
              value={wallpaperDim}
              onChange={(e) => setWallpaperDim(Number(e.target.value))}
              className="w-full accent-primary h-1 bg-muted rounded-lg appearance-none cursor-pointer"
            />
          </div>

          {/* Weichzeichner */}
          <div className="space-y-1">
            <div className="flex justify-between text-label-sm text-muted-foreground">
              <span>Unschärfe (Blur)</span>
              <span>{wallpaperBlur}px</span>
            </div>
            <input
              type="range"
              min="0"
              max="20"
              value={wallpaperBlur}
              onChange={(e) => setWallpaperBlur(Number(e.target.value))}
              className="w-full accent-primary h-1 bg-muted rounded-lg appearance-none cursor-pointer"
            />
          </div>
        </div>
      )}
    </div>
  )

  if (isModal) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-fade-in">
        <div className="w-full max-w-md p-5 rounded-2xl bg-card border border-border shadow-2xl">
          {content}
        </div>
      </div>
    )
  }

  return content
}
