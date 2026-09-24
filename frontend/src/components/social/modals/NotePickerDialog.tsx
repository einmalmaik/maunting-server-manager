import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { StickyNote } from 'lucide-react'
import { Dialog, DialogContent, Input } from '@/Singra/UI'
import type { NoteItem } from '@/pages/Notes'

interface NotePickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notes: NoteItem[]
  onPick: (note: NoteItem) => void
}

export function NotePickerDialog({ open, onOpenChange, notes, onPick }: NotePickerDialogProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const treffer = notes.filter((n) => n.title.toLowerCase().includes(search.toLowerCase()))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[75vh] flex flex-col p-4">
        <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
          <div className="flex items-center gap-2">
            <StickyNote className="w-4 h-4 text-status-warning" />
            <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.shareNote')}</span>
          </div>
        </div>

        <div className="py-2">
          <Input
            value={search}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
            placeholder={t('messenger.searchNote')}
            className="text-xs h-8"
          />
        </div>

        <div className="flex-1 overflow-y-auto space-y-2 py-2">
          {treffer.length === 0 ? (
            <p className="text-center py-6 text-xs text-on-surface-variant/70">
              Keine passenden Notizen gefunden.
            </p>
          ) : (
            treffer.map((n) => (
              <div
                key={n.id}
                onClick={() => onPick(n)}
                className="p-3 rounded-xl border border-outline-variant/30 hover:border-primary/50 hover:bg-surface-container transition-all cursor-pointer text-left"
              >
                <div className="font-semibold text-xs text-primary">{n.title}</div>
                <p className="text-label-sm text-on-surface-variant line-clamp-2 mt-0.5">
                  {n.content}
                </p>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
