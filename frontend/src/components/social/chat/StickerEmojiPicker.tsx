import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { sanitizeSvg } from '@/lib/sanitizeSvg'
import { CATEGORIZED_EMOJIS, IN_HOUSE_STICKERS, type InHouseSticker } from '@/services/stickerCatalog'

export type PickerReiter = 'stickers' | 'emojis'

interface StickerEmojiPickerProps {
  /** Bleibt über das Schliessen hinaus stehen, deshalb hält ihn die Seite. */
  reiter: PickerReiter
  onReiter: (reiter: PickerReiter) => void
  /** Ein Sticker geht sofort als eigene Nachricht raus. */
  onSticker: (sticker: InHouseSticker) => void
  /** Ein Emoji landet im Eingabefeld. */
  onEmoji: (emoji: string) => void
  onSchliessen: () => void
}

/** Die Auswahl aus Stickern und Emojis über der Eingabe. */
export function StickerEmojiPicker({ reiter, onReiter, onSticker, onEmoji, onSchliessen }: StickerEmojiPickerProps) {
  const { t } = useTranslation()

  return (
    <div className="mb-2 p-2.5 rounded-xl bg-surface-container border border-outline-variant/30 shadow-lg animate-slide-up">
      <div className="flex items-center justify-between pb-1.5 mb-1.5 border-b border-outline-variant/20">
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant={reiter === 'stickers' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => onReiter('stickers')}
            className="h-6 px-2.5 text-xs rounded-full"
          >
            Sticker
          </Button>
          <Button
            type="button"
            variant={reiter === 'emojis' ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => onReiter('emojis')}
            className="h-6 px-2.5 text-xs rounded-full"
          >
            Emojis
          </Button>
        </div>
        <button
          type="button"
          onClick={onSchliessen}
          className="p-1 rounded-md text-on-surface-variant hover:text-on-surface"
          aria-label={t('common.close')}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {reiter === 'stickers' ? (
        <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 max-h-48 overflow-y-auto p-1.5">
          {IN_HOUSE_STICKERS.map((stk) => (
            <button
              key={stk.id}
              type="button"
              onClick={() => onSticker(stk)}
              className="flex flex-col items-center justify-center p-1.5 rounded-xl hover:bg-surface-container-high transition-transform hover:scale-105"
              title={stk.label}
            >
              <div
                className="w-11 h-11 flex items-center justify-center"
                dangerouslySetInnerHTML={{ __html: sanitizeSvg(stk.svg) }}
              />
              <span className="text-label-sm text-on-surface-variant/80 truncate w-full text-center mt-1 font-medium">
                {stk.label}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-3 max-h-52 overflow-y-auto p-1.5">
          {CATEGORIZED_EMOJIS.map((cat) => (
            <div key={cat.category} className="space-y-1">
              <div className="text-label-sm font-bold text-on-surface-variant/70 uppercase tracking-wider px-1">
                {cat.category}
              </div>
              <div className="grid grid-cols-8 sm:grid-cols-12 gap-1">
                {cat.emojis.map((emoji) => (
                  <button
                    key={emoji}
                    type="button"
                    onClick={() => onEmoji(emoji)}
                    className="p-1 text-lg rounded-lg hover:bg-surface-container-high transition-transform hover:scale-125 flex items-center justify-center"
                  >
                    {emoji}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
