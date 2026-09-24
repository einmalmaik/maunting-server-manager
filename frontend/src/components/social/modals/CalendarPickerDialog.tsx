import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Calendar as CalendarIcon, Clock } from 'lucide-react'
import { Dialog, DialogContent, Input } from '@/Singra/UI'
import type { CalendarEventItem } from '@/pages/Calendar'

interface CalendarPickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  events: CalendarEventItem[]
  onPick: (event: CalendarEventItem) => void
}

export function CalendarPickerDialog({ open, onOpenChange, events, onPick }: CalendarPickerDialogProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const treffer = events.filter((ev) => ev.title.toLowerCase().includes(search.toLowerCase()))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[75vh] flex flex-col p-4">
        <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
          <div className="flex items-center gap-2">
            <CalendarIcon className="w-4 h-4 text-primary" />
            <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.shareEventTitle')}</span>
          </div>
        </div>

        <div className="py-2">
          <Input
            value={search}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
            placeholder={t('messenger.searchEvent')}
            className="text-xs h-8"
          />
        </div>

        <div className="flex-1 overflow-y-auto space-y-2 py-2">
          {treffer.length === 0 ? (
            <p className="text-center py-6 text-xs text-on-surface-variant/70">
              Keine Termine gefunden.
            </p>
          ) : (
            treffer.map((ev) => (
              <div
                key={ev.event_id || ev.id}
                onClick={() => onPick(ev)}
                className="p-3 rounded-xl border border-outline-variant/30 hover:border-primary/50 hover:bg-surface-container transition-all cursor-pointer text-left"
              >
                <div className="font-semibold text-xs text-primary">{ev.title}</div>
                <div className="text-label-sm text-on-surface-variant flex items-center gap-1 mt-0.5">
                  <Clock className="w-3 h-3" />
                  <span>
                    {new Date(ev.start).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
