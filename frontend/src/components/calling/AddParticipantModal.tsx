import React, { useEffect, useMemo, useState } from 'react'
import { Loader2, UserPlus } from 'lucide-react'
import { Button, Dialog, DialogContent, Input, Avatar } from '@/Singra/UI'
import { getFriends, type FriendItem } from '@/api/social'
import { toast } from '@/stores/toastStore'

export interface AddParticipantModalProps {
  isOpen: boolean
  onClose: () => void
  /** Wer schon im Raum ist — die stehen nicht mehr zur Auswahl. */
  bereitsImAnruf: readonly number[]
  onInvite: (userId: number) => Promise<void>
}

/**
 * Freunde in ein laufendes Gespräch holen.
 *
 * Nur bestätigte Freunde: das Backend prüft dieselbe Bedingung noch einmal,
 * diese Liste ist die Bequemlichkeit, nicht die Schranke.
 */
export const AddParticipantModal: React.FC<AddParticipantModalProps> = ({
  isOpen,
  onClose,
  bereitsImAnruf,
  onInvite,
}) => {
  const [freunde, setFreunde] = useState<FriendItem[]>([])
  const [laedt, setLaedt] = useState(false)
  const [suche, setSuche] = useState('')
  const [laeuft, setLaeuft] = useState<number | null>(null)

  useEffect(() => {
    if (!isOpen) return
    setLaedt(true)
    getFriends()
      .then(setFreunde)
      .catch(() => toast.error('Die Freundesliste konnte nicht geladen werden.'))
      .finally(() => setLaedt(false))
  }, [isOpen])

  const auswahl = useMemo(() => {
    const begriff = suche.trim().toLowerCase()
    return freunde
      .filter((freund) => !bereitsImAnruf.includes(freund.user_id))
      .filter((freund) => !begriff || freund.username.toLowerCase().includes(begriff))
  }, [freunde, bereitsImAnruf, suche])

  const einladen = async (freund: FriendItem) => {
    setLaeuft(freund.user_id)
    try {
      await onInvite(freund.user_id)
      toast.success(`${freund.username} wurde eingeladen.`)
    } catch {
      toast.error(`${freund.username} konnte nicht eingeladen werden.`)
    } finally {
      setLaeuft(null)
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-md p-6 bg-surface-container-high text-on-surface">
        <h3 className="font-headline text-base font-bold text-primary mb-1 flex items-center gap-2">
          <UserPlus className="w-4 h-4" />
          Teilnehmer hinzufügen
        </h3>
        <p className="text-xs text-on-surface-variant mb-4">
          Bei den Ausgewählten klingelt es sofort.
        </p>

        <Input
          value={suche}
          onChange={(e) => setSuche(e.target.value)}
          placeholder="Freund suchen"
          aria-label="Freund suchen"
          className="mb-3"
        />

        <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
          {laedt && (
            <div className="flex items-center justify-center py-6 text-on-surface-variant">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          )}
          {!laedt && auswahl.length === 0 && (
            <p className="py-6 text-center text-sm text-on-surface-variant">
              {freunde.length === 0
                ? 'Du hast noch keine Freunde in der Liste.'
                : 'Alle sind schon dabei.'}
            </p>
          )}
          {auswahl.map((freund) => (
            <div
              key={freund.user_id}
              className="flex items-center gap-3 rounded-xl border border-outline-variant/25 bg-surface-container px-2.5 py-2"
            >
              <Avatar
                src={freund.avatar_url}
                name={freund.username}
                size="sm"
                className="shrink-0"
              />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{freund.username}</span>
              <Button
                size="sm"
                onClick={() => void einladen(freund)}
                disabled={laeuft !== null}
              >
                {laeuft === freund.user_id ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  'Einladen'
                )}
              </Button>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
