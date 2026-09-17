/**
 * Was man mit einem Teilnehmer machen kann — per Klick auf seine Kachel.
 *
 * Zwei Ebenen, die nicht verwechselt werden dürfen:
 *
 * - **Für mich**: Lautstärke und stumm. Wirkt nur im eigenen Browser, braucht
 *   kein Recht und ist immer verfügbar, auch im Zweiergespräch.
 * - **Für alle**: Serverstumm und aus dem Anruf entfernen. Braucht ein Recht
 *   in der Gruppe, und der Server prüft es beim Ausführen noch einmal — was
 *   hier steht, entscheidet nur, ob der Knopf zu sehen ist.
 */

import { useEffect, useState } from 'react'
import { LogOut, MicOff, Volume2, VolumeX } from 'lucide-react'
import { Button, Dialog, DialogContent, Slider, Avatar } from '@/Singra/UI'
import { entferneAusAnruf, setzeServerStumm } from '@/api/calls'
import { toast } from '@/stores/toastStore'
import type { CallParticipant } from '@/stores/useCallStore'

export interface ParticipantMenuProps {
  participant: CallParticipant | null
  onClose: () => void
  onVolumeChange: (identity: string, wert: number) => void
  /** Raumname; ohne ihn gibt es keine Moderation (Zweiergespräch vor Aufbau). */
  raum: string | null
  darfStummschalten: boolean
  darfEntfernen: boolean
}

const MAX_PROZENT = 200

export function ParticipantMenu({
  participant,
  onClose,
  onVolumeChange,
  raum,
  darfStummschalten,
  darfEntfernen,
}: ParticipantMenuProps) {
  // Die Lautstärke vor dem Stummschalten, damit „wieder laut" den alten Wert
  // trifft und nicht stumpf auf 100 % springt.
  const [vorherigeLautstaerke, setVorherigeLautstaerke] = useState(1)
  const [laeuft, setLaeuft] = useState(false)

  useEffect(() => {
    if (participant && participant.volume > 0) setVorherigeLautstaerke(participant.volume)
  }, [participant?.identity])

  if (!participant) return null

  const stumm = participant.volume === 0
  const moderierbar = Boolean(raum) && !participant.isSelf
  const zeigeModeration = moderierbar && (darfStummschalten || darfEntfernen)

  const schalteStumm = () => {
    if (stumm) onVolumeChange(participant.identity, vorherigeLautstaerke || 1)
    else {
      setVorherigeLautstaerke(participant.volume || 1)
      onVolumeChange(participant.identity, 0)
    }
  }

  const serverStumm = async () => {
    if (!raum) return
    setLaeuft(true)
    try {
      await setzeServerStumm(raum, participant.userId, !participant.isMuted)
      toast.success(
        participant.isMuted
          ? `${participant.username} darf wieder sprechen.`
          : `${participant.username} ist stummgeschaltet.`,
      )
      onClose()
    } catch {
      toast.error('Das hat nicht geklappt. Vielleicht fehlt dir die Berechtigung.')
    } finally {
      setLaeuft(false)
    }
  }

  const entfernen = async () => {
    if (!raum) return
    setLaeuft(true)
    try {
      await entferneAusAnruf(raum, participant.userId)
      toast.success(`${participant.username} wurde aus dem Anruf entfernt.`)
      onClose()
    } catch {
      toast.error('Das hat nicht geklappt. Vielleicht fehlt dir die Berechtigung.')
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && onClose()}>
      <DialogContent className="max-w-sm">
        <div className="flex items-center gap-3 border-b border-outline-variant/30 p-5">
          <Avatar
            src={participant.avatarUrl}
            name={participant.username}
            size="lg"
            className="shrink-0"
          />
          <div className="min-w-0">
            <div className="truncate font-headline text-title-sm font-semibold text-on-surface">
              {participant.username}
            </div>
            <div className="text-xs text-on-surface-variant">
              {participant.isMuted ? 'Mikrofon aus' : 'Mikrofon an'}
            </div>
          </div>
        </div>

        {participant.isSelf ? (
          <p className="p-5 text-sm leading-relaxed text-on-surface-variant">
            Das bist du. Deine eigene Lautstärke stellst du in Profil → Audio ein.
          </p>
        ) : (
          <div className="space-y-5 p-5">
            <div>
              <Slider
                value={Math.round(participant.volume * 100)}
                min={0}
                max={MAX_PROZENT}
                step={5}
                onValueChange={(prozent) => onVolumeChange(participant.identity, prozent / 100)}
                label="Lautstärke für mich"
                hint={`${Math.round(participant.volume * 100)} %`}
              />
              <p className="mt-2 text-xs leading-relaxed text-on-surface-variant">
                Gilt nur hier bei dir. Die anderen im Anruf hören {participant.username}{' '}
                unverändert.
              </p>
            </div>

            <Button
              type="button"
              variant="secondary"
              onClick={schalteStumm}
              className="w-full justify-start gap-2"
            >
              {stumm ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
              {stumm ? 'Wieder hörbar machen' : 'Für mich stumm schalten'}
            </Button>

            {zeigeModeration && (
              <div className="space-y-2 border-t border-outline-variant/30 pt-5">
                <div className="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                  Für alle im Anruf
                </div>
                {darfStummschalten && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={laeuft}
                    onClick={() => void serverStumm()}
                    className="w-full justify-start gap-2"
                  >
                    <MicOff className="h-4 w-4" />
                    {participant.isMuted ? 'Wieder sprechen lassen' : 'Mikrofon abschalten'}
                  </Button>
                )}
                {darfEntfernen && (
                  <Button
                    type="button"
                    variant="destructive"
                    disabled={laeuft}
                    onClick={() => void entfernen()}
                    className="w-full justify-start gap-2"
                  >
                    <LogOut className="h-4 w-4" />
                    Aus dem Anruf entfernen
                  </Button>
                )}
                <p className="pt-1 text-xs leading-relaxed text-on-surface-variant">
                  Ein abgeschaltetes Mikrofon kann der Betroffene nicht selbst wieder
                  einschalten. Wer entfernt wird, kann erneut beitreten, solange er das
                  Recht dazu hat.
                </p>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
