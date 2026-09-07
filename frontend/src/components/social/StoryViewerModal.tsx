import React, { useState, useEffect, useRef } from 'react'
import {
  Avatar,
  Button,
} from '@/Singra/UI'
import {
  X,
  Trash2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Lock,
} from 'lucide-react'
import { type ChatStoryItem, deleteStory } from '@/api/social'
import { STORY_GRADIENTS } from './CreateStoryModal'
import { toast } from '@/stores/toastStore'

interface StoryViewerModalProps {
  stories: ChatStoryItem[]
  initialIndex?: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeleted?: (storyId: number) => void
}

export function StoryViewerModal({
  stories,
  initialIndex = 0,
  open,
  onOpenChange,
  onDeleted,
}: StoryViewerModalProps) {
  const [currentIndex, setCurrentIndex] = useState(initialIndex)
  const [progress, setProgress] = useState(0)
  const [isPaused, setIsPaused] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (open) {
      setCurrentIndex(Math.min(initialIndex, Math.max(0, stories.length - 1)))
      setProgress(0)
    }
  }, [open, initialIndex, stories.length])

  const currentStory = stories[currentIndex]

  // Auto-advance progress timer (6 seconds per story)
  useEffect(() => {
    if (!open || !currentStory || isPaused) {
      if (timerRef.current) clearInterval(timerRef.current)
      return
    }

    const intervalMs = 60
    const step = (intervalMs / 6000) * 100

    timerRef.current = setInterval(() => {
      setProgress((prev) => {
        if (prev + step >= 100) {
          // Advance to next or close if last
          if (currentIndex < stories.length - 1) {
            setCurrentIndex((i) => i + 1)
            return 0
          } else {
            onOpenChange(false)
            return 100
          }
        }
        return prev + step
      })
    }, intervalMs)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [open, currentIndex, stories.length, isPaused, currentStory, onOpenChange])

  // Reset progress when index changes
  useEffect(() => {
    setProgress(0)
  }, [currentIndex])

  const handlePrev = (e?: React.MouseEvent) => {
    e?.stopPropagation()
    if (currentIndex > 0) {
      setCurrentIndex((i) => i - 1)
    }
  }

  const handleNext = (e?: React.MouseEvent) => {
    e?.stopPropagation()
    if (currentIndex < stories.length - 1) {
      setCurrentIndex((i) => i + 1)
    } else {
      onOpenChange(false)
    }
  }

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!currentStory || !currentStory.is_self || deleting) return
    setDeleting(true)
    try {
      await deleteStory(currentStory.id)
      toast.success('Story gelöscht')
      onDeleted?.(currentStory.id)
      if (stories.length <= 1) {
        onOpenChange(false)
      } else if (currentIndex >= stories.length - 1) {
        setCurrentIndex((i) => Math.max(0, i - 1))
      }
    } catch {
      toast.error('Story konnte nicht gelöscht werden')
    } finally {
      setDeleting(false)
    }
  }

  if (!open || !currentStory) return null

  const backgroundClass = currentStory.media_url
    ? 'bg-black'
    : STORY_GRADIENTS[currentStory.background]?.class || 'bg-slate-900 text-white'

  const formatTimeAgo = (dateStr: string) => {
    const diffSec = Math.max(0, Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000))
    if (diffSec < 60) return 'Gerade eben'
    const diffMin = Math.floor(diffSec / 60)
    if (diffMin < 60) return `vor ${diffMin} Min.`
    const diffHr = Math.floor(diffMin / 60)
    return `vor ${diffHr} Std.`
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/90 backdrop-blur-md flex items-center justify-center p-2 sm:p-4 select-none"
      onClick={() => onOpenChange(false)}
    >
      <div
        className={`relative w-full max-w-sm sm:max-w-md aspect-9/16 max-h-[92vh] rounded-2xl overflow-hidden shadow-2xl flex flex-col justify-between p-4 sm:p-5 transition-all ${backgroundClass}`}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={() => setIsPaused(true)}
        onMouseUp={() => setIsPaused(false)}
        onTouchStart={() => setIsPaused(true)}
        onTouchEnd={() => setIsPaused(false)}
      >
        {/* Background Image if photo story */}
        {currentStory.media_url && (
          <img
            src={currentStory.media_url}
            alt="Story"
            className="absolute inset-0 w-full h-full object-cover"
          />
        )}

        {/* Top Header & Segmented Progress Bar */}
        <div className="relative z-10 space-y-2.5">
          {/* Progress Bars */}
          <div className="flex items-center gap-1 w-full">
            {stories.map((s, idx) => {
              const segProgress =
                idx < currentIndex ? 100 : idx === currentIndex ? progress : 0
              return (
                <div
                  key={s.id}
                  className="flex-1 h-1 bg-white/30 rounded-full overflow-hidden"
                >
                  <div
                    className="h-full bg-white transition-all ease-linear"
                    style={{ width: `${segProgress}%` }}
                  />
                </div>
              )
            })}
          </div>

          {/* User Info & Controls */}
          <div className="flex items-center justify-between text-white drop-shadow-md">
            <div className="flex items-center gap-2.5 min-w-0">
              <Avatar src={currentStory.avatar_url} name={currentStory.username} size="sm" />
              <div className="min-w-0">
                <div className="font-headline text-xs font-bold truncate flex items-center gap-1.5">
                  <span>{currentStory.username}</span>
                  {currentStory.is_self && (
                    <span className="text-[10px] bg-white/20 px-1.5 py-0.2 rounded font-normal">
                      Du
                    </span>
                  )}
                </div>
                <div className="text-[10px] opacity-80 flex items-center gap-1">
                  <Clock className="w-2.5 h-2.5" />
                  <span>{formatTimeAgo(currentStory.created_at)}</span>
                  <span>•</span>
                  <Lock className="w-2.5 h-2.5 text-emerald-300" />
                  <span>Ende-zu-Ende</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1">
              {currentStory.is_self && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={handleDelete}
                  disabled={deleting}
                  className="h-8 w-8 text-white hover:text-error hover:bg-white/10"
                  title="Story löschen"
                  aria-label="Story löschen"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              )}

              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => onOpenChange(false)}
                className="h-8 w-8 text-white hover:bg-white/10"
                aria-label="Schließen"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </div>
        </div>

        {/* Story Text Content */}
        <div className="relative z-10 flex-1 flex items-center justify-center p-4 text-center">
          <p className="font-headline text-xl sm:text-2xl font-bold text-white drop-shadow-lg leading-snug break-words max-h-72 overflow-y-auto no-scrollbar">
            {currentStory.content}
          </p>
        </div>

        {/* Navigation Touch Areas (Left & Right halves) */}
        <div
          className="absolute inset-y-16 left-0 w-1/3 cursor-pointer z-10 flex items-center pl-2 opacity-0 hover:opacity-75 transition-opacity"
          onClick={handlePrev}
          aria-label="Vorherige Story"
        >
          {currentIndex > 0 && (
            <div className="p-1 rounded-full bg-black/40 text-white backdrop-blur-xs">
              <ChevronLeft className="w-5 h-5" />
            </div>
          )}
        </div>

        <div
          className="absolute inset-y-16 right-0 w-1/3 cursor-pointer z-10 flex items-center justify-end pr-2 opacity-0 hover:opacity-75 transition-opacity"
          onClick={handleNext}
          aria-label="Nächste Story"
        >
          <div className="p-1 rounded-full bg-black/40 text-white backdrop-blur-xs">
            <ChevronRight className="w-5 h-5" />
          </div>
        </div>

        {/* Bottom Expiration Footer */}
        <div className="relative z-10 text-[10px] text-white/70 text-center drop-shadow">
          Gültig bis {new Date(currentStory.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} Uhr
        </div>
      </div>
    </div>
  )
}
