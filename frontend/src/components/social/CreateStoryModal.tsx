import React, { useState } from 'react'
import {
  Dialog,
  DialogContent,
  Button,
  Input,
} from '@/Singra/UI'
import {
  Sparkles,
  Camera,
  X,
  Upload,
  Clock,
  Send,
} from 'lucide-react'
import { createStory, type ChatStoryItem } from '@/api/social'
import { CameraSnapshotModal } from './CameraSnapshotModal'
import { toast } from '@/stores/toastStore'

export const STORY_GRADIENTS: Record<string, { label: string; class: string }> = {
  'gradient-1': { label: 'Violett', class: 'bg-gradient-to-tr from-purple-700 via-indigo-600 to-violet-900 text-white' },
  'gradient-2': { label: 'Ozean', class: 'bg-gradient-to-tr from-sky-600 via-cyan-600 to-blue-800 text-white' },
  'gradient-3': { label: 'Smaragd', class: 'bg-gradient-to-tr from-emerald-600 via-teal-700 to-emerald-900 text-white' },
  'gradient-4': { label: 'Sonnenuntergang', class: 'bg-gradient-to-tr from-amber-500 via-orange-600 to-rose-700 text-white' },
  'gradient-5': { label: 'Cyber', class: 'bg-gradient-to-tr from-pink-600 via-purple-700 to-indigo-900 text-white' },
  'gradient-6': { label: 'Mitternacht', class: 'bg-gradient-to-tr from-slate-900 via-indigo-950 to-neutral-900 text-white' },
  'gradient-7': { label: 'Vulkan', class: 'bg-gradient-to-tr from-rose-700 via-red-600 to-amber-700 text-white' },
  'gradient-8': { label: 'Neon-Wald', class: 'bg-gradient-to-tr from-teal-500 via-emerald-600 to-cyan-800 text-white' },
}

interface CreateStoryModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (story: ChatStoryItem) => void
  initialMode?: 'text' | 'photo'
  initialPhotoUrl?: string | null
}

export function CreateStoryModal({
  open,
  onOpenChange,
  onCreated,
  initialMode = 'text',
  initialPhotoUrl = null,
}: CreateStoryModalProps) {
  const [content, setContent] = useState('')
  const [selectedGradient, setSelectedGradient] = useState<string>('gradient-1')
  const [photoDataUrl, setPhotoDataUrl] = useState<string | null>(initialPhotoUrl)
  const [isCameraOpen, setIsCameraOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  React.useEffect(() => {
    if (initialPhotoUrl) {
      setPhotoDataUrl(initialPhotoUrl)
    }
  }, [initialPhotoUrl])

  React.useEffect(() => {
    if (open && initialMode === 'photo' && !photoDataUrl && !initialPhotoUrl) {
      setIsCameraOpen(true)
    }
  }, [open, initialMode, photoDataUrl, initialPhotoUrl])

  const fileInputRef = React.useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setPhotoDataUrl(reader.result)
      }
    }
    reader.readAsDataURL(file)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = content.trim()
    if (!trimmed && !photoDataUrl) {
      toast.error('Bitte Text oder Foto für die Story hinzufügen.')
      return
    }

    setSubmitting(true)
    try {
      const newStory = await createStory({
        content: trimmed || 'Status-Update',
        media_url: photoDataUrl || undefined,
        background: selectedGradient,
      })
      toast.success('Story erfolgreich geteilt!')
      setContent('')
      setPhotoDataUrl(null)
      onCreated(newStory)
      onOpenChange(false)
    } catch {
      toast.error('Story konnte nicht geteilt werden.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent showCloseButton={false} className="max-w-md p-4 bg-surface border-outline-variant/30 flex flex-col">
          <div className="flex items-center justify-between pb-2 border-b border-outline-variant/20 mb-3">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-primary" />
              <span className="font-headline text-body-sm font-bold text-primary">Status erstellen</span>
            </div>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="p-1 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
              aria-label="Schließen"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            {/* Live Story Preview Card in 16:9 Format */}
            <div className="flex justify-center w-full">
              <div
                className={`relative aspect-16/9 w-full max-w-sm sm:max-w-md max-h-[45dvh] rounded-2xl p-4 flex flex-col justify-between shadow-2xl overflow-hidden transition-all border border-outline-variant/30 ${
                  photoDataUrl ? 'bg-black text-white' : STORY_GRADIENTS[selectedGradient]?.class || 'bg-slate-900 text-white'
                }`}
              >
                {photoDataUrl && (
                  <img
                    src={photoDataUrl}
                    alt="Story Preview"
                    className="absolute inset-0 w-full h-full object-cover"
                  />
                )}

                {/* Top watermark / expiry tag */}
                <div className="relative z-10 flex items-center justify-between text-[11px] font-semibold opacity-95">
                  <span className="flex items-center gap-1 bg-black/50 px-2 py-0.5 rounded-full backdrop-blur-md shadow-xs">
                    <Clock className="w-3 h-3 text-primary-fixed" />
                    <span>24h Status</span>
                  </span>
                  {photoDataUrl && (
                    <button
                      type="button"
                      onClick={() => setPhotoDataUrl(null)}
                      className="p-1 rounded-full bg-black/60 text-white hover:bg-black/90 transition-colors shadow-xs"
                      title="Foto entfernen"
                      aria-label="Foto entfernen"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                {/* Story Text */}
                <div className="relative z-10 flex-1 flex items-center justify-center text-center px-2 py-4">
                  <p className="font-headline text-base sm:text-lg font-bold break-words drop-shadow-md max-h-40 overflow-y-auto no-scrollbar">
                    {content || (photoDataUrl ? '' : 'Tippe unten, um Text hinzuzufügen')}
                  </p>
                </div>

                {/* Bottom tag */}
                <div className="relative z-10 text-[10px] opacity-75 text-center truncate">
                  Ende-zu-Ende verschlüsselt
                </div>
              </div>
            </div>

            {/* Quick Action Selector: Kamera & Bild hochladen */}
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setIsCameraOpen(true)}
                className="h-9 text-xs gap-2 font-medium justify-center bg-surface-container hover:bg-surface-container-high border border-outline-variant/30"
              >
                <Camera className="w-4 h-4 text-primary" />
                <span>Kamera</span>
              </Button>

              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
                className="h-9 text-xs gap-2 font-medium justify-center bg-surface-container hover:bg-surface-container-high border border-outline-variant/30"
              >
                <Upload className="w-4 h-4 text-primary" />
                <span>Bild wählen</span>
              </Button>

              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleFileChange}
              />
            </div>

            {/* Content Input */}
            <div className="space-y-1">
              <Input
                value={content}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setContent(e.target.value)}
                placeholder="Was machst du gerade? (Text oder Bildunterschrift)"
                maxLength={400}
                className="text-xs h-9 bg-surface-container-high/60 border-outline-variant/30"
              />
            </div>

            {/* Color / Gradient Selector (if no photo) */}
            {!photoDataUrl && (
              <div className="flex items-center gap-2.5 py-1 px-1">
                <span className="text-[11px] font-semibold text-on-surface-variant/90 shrink-0">Farbe:</span>
                <div className="flex items-center gap-2.5 overflow-x-auto no-scrollbar py-1">
                  {Object.entries(STORY_GRADIENTS).map(([k, grad]) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setSelectedGradient(k)}
                      className={`w-7 h-7 rounded-full shrink-0 ${grad.class} transition-all duration-200 shadow-xs cursor-pointer ${
                        selectedGradient === k
                          ? 'ring-2 ring-primary ring-offset-2 ring-offset-surface scale-110'
                          : 'opacity-80 hover:opacity-100 hover:scale-105'
                      }`}
                      title={grad.label}
                      aria-label={`Farbverlauf ${grad.label}`}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Submit Button */}
            <div className="flex items-center justify-end pt-1">
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={(!content.trim() && !photoDataUrl) || submitting}
                className="w-full sm:w-auto h-9 px-5 text-xs gap-1.5 font-semibold"
              >
                <Send className="w-3.5 h-3.5" />
                <span>{submitting ? 'Teile Status…' : 'Status teilen'}</span>
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Snapshot Modal */}
      <CameraSnapshotModal
        open={isCameraOpen}
        onOpenChange={setIsCameraOpen}
        onCapture={(dataUrl) => setPhotoDataUrl(dataUrl)}
      />
    </>
  )
}
