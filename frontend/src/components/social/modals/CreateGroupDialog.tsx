import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Sparkles, UsersRound } from 'lucide-react'
import { Button, Dialog, DialogContent, Input } from '@/Singra/UI'

interface CreateGroupDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Legt die Gruppe an. `true` heisst angelegt, dann leert sich das Formular. */
  onCreate: (name: string, beschreibung: string | null) => Promise<boolean>
}

export function CreateGroupDialog({ open, onOpenChange, onCreate }: CreateGroupDialogProps) {
  const { t } = useTranslation()
  const [groupName, setGroupName] = useState('')
  const [groupDesc, setGroupDesc] = useState('')
  const [creating, setCreating] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const name = groupName.trim()
    if (!name) return
    setCreating(true)
    try {
      if (await onCreate(name, groupDesc.trim() || null)) {
        setGroupName('')
        setGroupDesc('')
      }
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md p-5">
        <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
          <div className="flex items-center gap-2">
            <UsersRound className="w-5 h-5 text-primary" />
            <span className="font-headline text-body-md font-bold text-primary">{t('messenger.newGroup')}</span>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 pt-3">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-on-surface">{t('messenger.groupNameLabel')}</label>
            <Input
              value={groupName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupName(e.target.value)}
              placeholder={t('messenger.groupNamePlaceholder')}
              required
              className="text-xs h-9"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-on-surface">{t('messenger.groupDescLabel')}</label>
            <Input
              value={groupDesc}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setGroupDesc(e.target.value)}
              placeholder={t('messenger.groupDescPlaceholder')}
              className="text-xs h-9"
            />
          </div>

          <div className="p-3 rounded-xl bg-surface-container-high/60 border border-outline-variant/30 text-xs text-on-surface-variant space-y-1">
            <div className="flex items-center gap-1.5 font-semibold text-primary">
              <Sparkles className="w-3.5 h-3.5" />
              <span>{t('messenger.groupE2eeLabel')}</span>
            </div>
            <p className="text-label-sm">
              {t('messenger.groupInviteHint')}
            </p>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onOpenChange(false)}
              disabled={creating}
            >
              Abbrechen
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={!groupName.trim() || creating}
            >
              {creating ? t('messenger.creating') : t('messenger.createGroup')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
