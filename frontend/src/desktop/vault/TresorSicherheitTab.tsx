/**
 * Tresor-Sperre und biometrischer Schnelleinstieg.
 *
 * Stand bis 09/2026 als zwei Karten unter „Konto", zwischen Profilbild und
 * Zeitzone. Dorthin gehörte es nie: im Konto-Reiter steht, wer man ist, hier
 * steht, wie der Tresor auf diesem Rechner zugeht. Seit der Messenger einen
 * eigenen Reiter für dasselbe hat, bekommt der Tresor auch einen.
 *
 * Die Mechanik der Sperre teilen sich beide (`services/autoSperre`), die Zahlen
 * nicht: wer seinen Tresor nach fünf Minuten zumacht, will den Messenger
 * deshalb noch lange nicht zumachen.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Fingerprint, ShieldCheck } from 'lucide-react'

import { Button, Dropdown, type DropdownOption, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { useVaultStore } from './vaultStore'

export function TresorSicherheitTab() {
  const { t } = useTranslation()

  const {
    isInitialized,
    isUnlocked,
    autoLockMinutes,
    lockOnWindowBlur,
    isBiometricsSupported,
    isBiometricsEnabled,
    setAutoLockMinutes,
    setLockOnWindowBlur,
    enableBiometrics,
    disableBiometrics,
    checkBiometricsSupport,
  } = useVaultStore()

  const [biometricsModalOpen, setBiometricsModalOpen] = useState(false)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [biometricsLoading, setBiometricsLoading] = useState(false)

  useEffect(() => {
    void checkBiometricsSupport()
  }, [checkBiometricsSupport])

  /**
   * Die Werte bleiben, wie sie waren — eine gespeicherte `10` soll weiter
   * zehn Minuten heißen.
   *
   * Die Beschriftung der `0` nicht: sie hieß „Sofort beim Verlassen" und tat
   * das Gegenteil. `checkAutoLock` steigt bei `autoLockMinutes <= 0` sofort
   * aus, der Tresor bleibt also offen. Das Verlassen regelt der Schalter
   * darunter, und zwar unabhängig von dieser Zahl. Wer „Sofort" gewählt hat,
   * weil er es für die sicherste Einstellung hielt, hatte die unsicherste.
   */
  const autoLockOptions: DropdownOption[] = [
    { value: '0', label: t('mss.vault.autolock.disabled', 'Nie (nur beim Verlassen)') },
    { value: '5', label: t('mss.vault.autolock.5min', '5 Minuten Inaktivität') },
    { value: '10', label: t('mss.vault.autolock.10min', '10 Minuten Inaktivität') },
    { value: '15', label: t('mss.vault.autolock.15min', '15 Minuten Inaktivität') },
    { value: '30', label: t('mss.vault.autolock.30min', '30 Minuten Inaktivität') },
    { value: '60', label: t('mss.vault.autolock.60min', '1 Stunde Inaktivität') },
  ]

  const handleBiometricsToggle = async (checked: boolean) => {
    if (!checked) {
      await disableBiometrics()
      toast.success('Biometrischer Schnelleinstieg deaktiviert')
      return
    }
    setMasterPasswordInput('')
    setBiometricsModalOpen(true)
  }

  const handleConfirmBiometrics = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!masterPasswordInput) return
    setBiometricsLoading(true)
    try {
      const ok = await enableBiometrics(masterPasswordInput)
      if (ok) {
        toast.success('Biometrischer Schnelleinstieg aktiviert')
        setBiometricsModalOpen(false)
        setMasterPasswordInput('')
      } else {
        toast.error('Konnte Biometrie nicht aktivieren. Prüfe das Master-Passwort.')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Konnte Biometrie nicht aktivieren. Prüfe das Master-Passwort.'
      toast.error(msg)
    } finally {
      setBiometricsLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Automatische Sperre */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Tresor-Sicherheit & Auto-Lock</h2>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <label className="text-xs font-medium text-on-surface">Automatische Sperre</label>
            </div>
            <div className="w-full sm:w-56">
              <Dropdown
                options={autoLockOptions}
                value={String(autoLockMinutes)}
                onChange={(val) => setAutoLockMinutes(Number(val))}
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">Beim Fensterwechsel / App-Verlassen sperren</span>
              <p className="text-[11px] text-on-surface-variant">
                Sperrt den Passwort-Manager sofort, sobald das Fenster verlassen oder die App in den Hintergrund gelegt wird.
              </p>
            </div>
            <Switch
              checked={lockOnWindowBlur}
              onCheckedChange={setLockOnWindowBlur}
            />
          </div>
        </div>
      </div>

      {/* Biometrischer Schnelleinstieg */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Fingerprint className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Biometrischer Schnelleinstieg</h2>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          {!isInitialized ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Passwort-Manager ist auf diesem Gerät noch nicht eingerichtet.
            </div>
          ) : !isUnlocked ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Tresor ist gesperrt. Bitte zuerst entsperren.
            </div>
          ) : null}

          <div className="flex items-center justify-between gap-4">
            <div>
              <span className="text-xs font-medium text-on-surface">
                Biometrische Authentifizierung aktivieren
              </span>
              <p className="text-[11px] text-on-surface-variant">
                Windows Hello / nativer Hardware-Schlüsselspeicher. Schlüssel werden niemals im Browser oder ungesichert gespeichert.
              </p>
            </div>
            <Switch
              checked={isBiometricsEnabled && isBiometricsSupported}
              onCheckedChange={(checked: boolean) => void handleBiometricsToggle(checked)}
              disabled={!isBiometricsSupported || !isInitialized || !isUnlocked}
            />
          </div>

          {!isBiometricsSupported && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Kein biometrischer Sensor oder nativer Hardware-Tresor auf diesem Gerät verfügbar.
            </div>
          )}
        </div>
      </div>

      {/* Biometrie Aktivierungs-Modal */}
      {biometricsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/60 backdrop-blur-xs">
          <div className="w-full max-w-sm rounded-2xl bg-surface-container border border-outline-variant/30 p-5 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Fingerprint className="h-4 w-4" />
              </div>
              <h3 className="text-sm font-semibold text-on-surface">Biometrie einrichten</h3>
            </div>
            <p className="text-xs text-on-surface-variant">
              Master-Passwort zur Bestätigung eingeben.
            </p>
            <form onSubmit={handleConfirmBiometrics} className="space-y-3">
              <input
                type="password"
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort"
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface focus:outline-none focus:border-primary"
                autoFocus
                required
              />
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setBiometricsModalOpen(false)}
                  disabled={biometricsLoading}
                >
                  Abbrechen
                </Button>
                <Button
                  type="submit"
                  disabled={biometricsLoading || !masterPasswordInput}
                >
                  {biometricsLoading ? 'Prüfe...' : 'Aktivieren'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
