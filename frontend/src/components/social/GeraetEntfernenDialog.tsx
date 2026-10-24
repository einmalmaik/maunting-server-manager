import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2 } from 'lucide-react'

import type { E2eeGeraetItem, EntfernenNachweis } from '@/api/social'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  PasswordInput,
} from '@/Singra/UI'
import { entferneGeraet } from '@/services/e2eeGeraet'
import { useZweitfaktor, ZweitfaktorFeld } from '@/components/auth/ZweitfaktorNachweis'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

interface Props {
  geraet: E2eeGeraetItem
  /** Die Vergleichszahl aus der Liste — sie steht auch auf dem Gerät selbst. */
  sicherheitsnummer?: string
  onOpenChange: (offen: boolean) => void
  onEntfernt: () => Promise<void> | void
}

/**
 * Die Rückfrage vor dem Entfernen eines Geräts — mit Nachweis, nicht nur mit Klick.
 *
 * Bis 10/2026 genügte „Abbrechen / Entfernen“. Wer eine offene Sitzung vorfand,
 * warf damit jedes echte Gerät aus dem Verzeichnis, und danach war sein eigenes
 * das erste — und damit freigegeben. Deshalb kommt jetzt die Kaskade aus dem
 * Fehlerbericht, in dieser Reihenfolge und nur mit dem, was das Konto hat:
 *
 * 1. eingerichteter zweiter Faktor — Passkey, sonst Authenticator-Code,
 * 2. sonst das Passwort,
 * 3. nur bei einem Social-Login-Konto ohne beides: die Sitzung selbst.
 *
 * Die Reihenfolge entscheidet `useZweitfaktor`: es wählt den zuletzt benutzten
 * Faktor und lässt zwischen Passkey und App umschalten, wenn das Gerät keinen
 * Passkey bedienen kann. Der Server prüft dieselbe Kaskade noch einmal an den
 * gespeicherten Faktoren (`_entfernen_nachweis`) — was hier steht, ist Bedienung,
 * nicht Beweis.
 *
 * Die eigene Unterschrift (`entfernenDaten`) kommt aus `entferneGeraet`; ohne
 * sie nimmt der Server ein freigegebenes Gerät nicht an.
 */
export function GeraetEntfernenDialog({
  geraet,
  sicherheitsnummer,
  onOpenChange,
  onEntfernt,
}: Props) {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const faktor = useZweitfaktor()
  const [passwort, setPasswort] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const titelId = useId()
  const textId = useId()
  const feldId = useId()

  // Ein wartendes Gerät hat nie etwas bekommen. Der Server verlangt dafür
  // keinen Nachweis, und die Kopplung mit falscher Sicherheitsnummer muss
  // sofort abbrechbar bleiben — hier also kein Feld davor.
  const wartet = geraet.is_approved === false
  const mitFaktor = !wartet && faktor.methoden.length > 0
  const mitPasswort = !wartet && !mitFaktor && user?.has_password !== false
  const bereit = mitFaktor ? faktor.bereit : mitPasswort ? passwort.length > 0 : true

  const nachweis = async (): Promise<EntfernenNachweis> => {
    if (mitFaktor) return await faktor.nachweis('e2ee_remove')
    if (mitPasswort) return { password: passwort }
    return {}
  }

  const bestaetigen = async () => {
    setLaeuft(true)
    try {
      await entferneGeraet(geraet, await nachweis())
      toast.success(t('profile.e2eeDevices.removed'))
      await onEntfernt()
      onOpenChange(false)
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && onOpenChange(false)}>
      <DialogContent aria-labelledby={titelId} aria-describedby={textId}>
        <DialogHeader>
          <DialogTitle id={titelId}>{t('profile.e2eeDevices.removeTitle')}</DialogTitle>
          <DialogDescription id={textId}>{t('profile.e2eeDevices.removeMessage')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 p-6">
          <div className="space-y-1">
            <p className="text-sm font-medium text-on-surface">
              {geraet.label || t('profile.e2eeDevices.unnamed')}
            </p>
            {sicherheitsnummer && (
              <p className="font-mono text-xs text-on-surface-variant">
                {t('profile.e2eeDevices.safetyNumber')}: {sicherheitsnummer}
              </p>
            )}
          </div>

          {wartet ? (
            <p className="text-sm text-on-surface-variant">
              {t('profile.e2eeDevices.removePendingHelp')}
            </p>
          ) : mitFaktor ? (
            <>
              <p className="text-sm text-on-surface-variant">
                {t('profile.e2eeDevices.removeStepUpHelp')}
              </p>
              <ZweitfaktorFeld
                faktor={faktor}
                id={feldId}
                label={t('profile.e2eeDevices.removeTotp')}
                disabled={laeuft}
              />
            </>
          ) : mitPasswort ? (
            <>
              <p className="text-sm text-on-surface-variant">
                {t('profile.e2eeDevices.removeStepUpHelp')}
              </p>
              <PasswordInput
                id={feldId}
                label={t('profile.e2eeDevices.removePassword')}
                autoComplete="current-password"
                value={passwort}
                onChange={(e) => setPasswort(e.target.value)}
                disabled={laeuft}
              />
            </>
          ) : (
            <p className="text-sm text-on-surface-variant">
              {t('profile.e2eeDevices.removeSessionHelp')}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={laeuft}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            disabled={!bereit || laeuft}
            onClick={() => void bestaetigen()}
          >
            <Trash2 className="h-4 w-4 mr-1" aria-hidden="true" />
            {t('profile.e2eeDevices.remove')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
