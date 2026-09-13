/**
 * Der Wiederherstellungsschlüssel des Messengers.
 *
 * Drei Situationen, ein Fenster: einrichten (das Konto hat noch keinen
 * Schlüssel), entsperren (es hat einen, dieses Gerät kennt ihn nicht) und
 * wechseln. Die Reihenfolge beim Einrichten ist bewusst zweistufig — der
 * Schlüssel wird gezeigt und muss bestätigt werden, bevor das Fenster zugeht.
 * Danach existiert er nirgends mehr, auch nicht beim Anbieter.
 */

import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  Button,
  Input,
  Checkbox,
} from '@/Singra/UI'
import { KeyRound, Copy, Check, ShieldCheck, LockKeyhole, AlertTriangle } from 'lucide-react'
import {
  createIdentity,
  unlockWithRecoveryKey,
  rotateRecoveryKey,
  type IdentityState,
} from '@/services/e2eeIdentity'

interface MessengerSchluesselDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentUserId: number
  state: IdentityState
  /** Läuft, sobald sich der Zustand geändert hat, damit der Messenger neu lädt. */
  onIdentityChanged: () => void
}

type Phase = 'intro' | 'schluessel-zeigen' | 'entsperren'

export function MessengerSchluesselDialog({
  open,
  onOpenChange,
  currentUserId,
  state,
  onIdentityChanged,
}: MessengerSchluesselDialogProps) {
  const [phase, setPhase] = useState<Phase>('intro')
  const [recoveryKey, setRecoveryKey] = useState<string | null>(null)
  const [gesichert, setGesichert] = useState(false)
  const [eingabe, setEingabe] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const [kopiert, setKopiert] = useState(false)

  const zuruecksetzen = () => {
    setPhase('intro')
    setRecoveryKey(null)
    setGesichert(false)
    setEingabe('')
    setFehler(null)
    setLaeuft(false)
    setKopiert(false)
  }

  const schliessen = (next: boolean) => {
    if (!next) zuruecksetzen()
    onOpenChange(next)
  }

  const handleErstellen = async () => {
    setLaeuft(true)
    setFehler(null)
    try {
      const { recoveryKey: key } = await createIdentity(currentUserId)
      setRecoveryKey(key)
      setPhase('schluessel-zeigen')
    } catch (err) {
      setFehler(fehlertext(err, 'Der Schlüssel konnte nicht angelegt werden.'))
    } finally {
      setLaeuft(false)
    }
  }

  const handleWechseln = async () => {
    setLaeuft(true)
    setFehler(null)
    try {
      const { recoveryKey: key } = await rotateRecoveryKey(currentUserId)
      setRecoveryKey(key)
      setPhase('schluessel-zeigen')
    } catch (err) {
      setFehler(fehlertext(err, 'Der Schlüssel konnte nicht gewechselt werden.'))
    } finally {
      setLaeuft(false)
    }
  }

  const handleEntsperren = async () => {
    if (!eingabe.trim()) return
    setLaeuft(true)
    setFehler(null)
    try {
      await unlockWithRecoveryKey(currentUserId, eingabe)
      onIdentityChanged()
      schliessen(false)
    } catch (err) {
      setFehler(fehlertext(err, 'Dieser Wiederherstellungsschlüssel passt nicht zu deinem Konto.'))
    } finally {
      setLaeuft(false)
    }
  }

  const handleKopieren = async () => {
    if (!recoveryKey) return
    try {
      await navigator.clipboard.writeText(recoveryKey)
      setKopiert(true)
      setTimeout(() => setKopiert(false), 2000)
    } catch {
      setFehler('Kopieren hat nicht geklappt. Schreib den Schlüssel bitte von Hand ab.')
    }
  }

  const handleFertig = () => {
    onIdentityChanged()
    schliessen(false)
  }

  const zeigeEntsperren = phase === 'entsperren' || (phase === 'intro' && state === 'locked')

  return (
    <Dialog open={open} onOpenChange={schliessen}>
      <DialogContent className="max-w-md w-full bg-surface-container border border-outline-variant/40 shadow-xl rounded-2xl p-5 space-y-4">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base font-bold text-on-surface">
            <KeyRound className="w-5 h-5 text-primary" />
            Wiederherstellungsschlüssel
          </DialogTitle>
          <DialogDescription className="text-xs text-on-surface-variant">
            Deine Nachrichten werden auf deinem Gerät verschlüsselt. Der Schlüssel dazu gehört
            deinem Konto, nicht einem einzelnen Gerät.
          </DialogDescription>
        </DialogHeader>

        {phase === 'schluessel-zeigen' && recoveryKey ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-xl bg-status-warning/10 border border-status-warning/30">
              <AlertTriangle className="w-4 h-4 text-status-warning shrink-0 mt-0.5" />
              <p className="text-xs text-on-surface">
                Dieser Schlüssel wird dir genau einmal gezeigt. Wir können ihn nicht nachschlagen.
                Ohne ihn bleibt dein Verlauf auf neuen Geräten unlesbar.
              </p>
            </div>

            <div className="p-3 rounded-xl bg-surface-container-high border border-outline-variant">
              <p className="font-mono text-base text-on-surface tracking-[0.2em] text-center break-all select-all">
                {recoveryKey}
              </p>
            </div>

            <Button variant="secondary" className="w-full" onClick={handleKopieren}>
              {kopiert ? (
                <>
                  <Check className="w-4 h-4 mr-2" />
                  Kopiert
                </>
              ) : (
                <>
                  <Copy className="w-4 h-4 mr-2" />
                  Kopieren
                </>
              )}
            </Button>

            <div className="flex items-start gap-2 text-xs text-on-surface">
              <Checkbox
                checked={gesichert}
                onCheckedChange={setGesichert}
                className="mt-0.5"
                id="e2ee-schluessel-gesichert"
              />
              <label htmlFor="e2ee-schluessel-gesichert" className="cursor-pointer">
                Ich habe den Schlüssel notiert oder in meinem Passwortmanager gespeichert.
              </label>
            </div>

            {fehler && <p className="text-xs text-status-destructive">{fehler}</p>}

            <DialogFooter>
              <Button className="w-full" disabled={!gesichert} onClick={handleFertig}>
                Fertig
              </Button>
            </DialogFooter>
          </div>
        ) : zeigeEntsperren ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-xl bg-surface-container-high border border-outline-variant">
              <LockKeyhole className="w-4 h-4 text-primary shrink-0 mt-0.5" />
              <p className="text-xs text-on-surface-variant">
                Dieses Gerät kennt deinen Schlüssel noch nicht. Gib ihn einmal ein, danach bleibt er
                hier gespeichert.
              </p>
            </div>

            <Input
              label="Wiederherstellungsschlüssel"
              value={eingabe}
              onChange={(e) => {
                setEingabe(e.target.value)
                setFehler(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleEntsperren()
              }}
              placeholder="A2C4E-F7H9J-K3M5N-P8Q2R"
              autoComplete="off"
              spellCheck={false}
              className="font-mono tracking-widest"
              error={fehler ?? undefined}
            />

            {fehler && <p className="text-xs text-status-destructive">{fehler}</p>}

            <DialogFooter>
              <Button
                className="w-full"
                disabled={laeuft || !eingabe.trim()}
                onClick={handleEntsperren}
              >
                {laeuft ? 'Wird geprüft …' : 'Entsperren'}
              </Button>
            </DialogFooter>
          </div>
        ) : state === 'needs-setup' ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-xl bg-surface-container-high border border-outline-variant">
              <ShieldCheck className="w-4 h-4 text-primary shrink-0 mt-0.5" />
              <p className="text-xs text-on-surface-variant">
                Du bekommst gleich einen Schlüssel, mit dem du deinen Verlauf auf jedem weiteren
                Gerät öffnest. Er verlässt dieses Fenster nur, wenn du ihn kopierst.
              </p>
            </div>

            {fehler && <p className="text-xs text-status-destructive">{fehler}</p>}

            <DialogFooter>
              <Button className="w-full" disabled={laeuft} onClick={handleErstellen}>
                {laeuft ? 'Wird angelegt …' : 'Schlüssel erstellen'}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-xl bg-surface-container-high border border-outline-variant">
              <ShieldCheck className="w-4 h-4 text-primary shrink-0 mt-0.5" />
              <p className="text-xs text-on-surface-variant">
                Dieses Gerät ist entsperrt. Wenn du deinen Schlüssel verloren hast oder ihn jemand
                anderes kennen könnte, erstell einen neuen. Dein Verlauf bleibt dabei erhalten.
              </p>
            </div>

            {fehler && <p className="text-xs text-status-destructive">{fehler}</p>}

            <DialogFooter className="flex-col gap-2">
              <Button variant="secondary" className="w-full" disabled={laeuft} onClick={handleWechseln}>
                {laeuft ? 'Wird erstellt …' : 'Neuen Schlüssel erstellen'}
              </Button>
              <Button className="w-full" onClick={() => schliessen(false)}>
                Schließen
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

/**
 * Trennt „falscher Schlüssel" von „Verbindung weg". Beides sieht für den
 * Benutzer gleich aus, aber nur eines davon löst er durch Nachdenken.
 */
function fehlertext(err: unknown, standard: string): string {
  const msg = err instanceof Error ? err.message : String(err)
  if (/fetch|network|Failed to fetch|NetworkError/i.test(msg)) {
    return 'Keine Verbindung zum Server. Versuch es gleich noch einmal.'
  }
  if (/409/.test(msg)) {
    return 'Ein anderes Gerät war schneller. Lade die Seite neu und versuch es erneut.'
  }
  return standard
}
