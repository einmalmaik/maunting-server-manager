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

import { sperrfristOptionen } from '@/services/autoSperre'
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

  // Dieselbe Auswahl wie im Messenger-Reiter, aus derselben Quelle. Vorher
  // standen hier sechs eigene Einträge mit eigener Grammatik („5 Minuten
  // Inaktivität" gegen „Nach 5 Minuten" drüben) und einer falschen: die `0`
  // hieß „Sofort beim Verlassen" und bedeutet „nie".
  const autoLockOptions: DropdownOption[] = sperrfristOptionen(t)

  const handleBiometricsToggle = async (checked: boolean) => {
    if (!checked) {
      await disableBiometrics()
      toast.success(t('mss.vault.biometrieAusToast'))
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
        toast.success(t('mss.vault.biometrieAn'))
        setBiometricsModalOpen(false)
        setMasterPasswordInput('')
      } else {
        toast.error(t('mss.vault.biometrieFehler'))
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('mss.vault.biometrieFehler')
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
            <h2 className="text-sm font-semibold text-on-surface">{t('mss.vault.titel')}</h2>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <label className="text-xs font-medium text-on-surface">{t('mss.vault.autoSperre')}</label>
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
              <span className="text-xs font-medium text-on-surface">{t('mss.vault.beiFensterwechsel')}</span>
              <p className="text-[11px] text-on-surface-variant">
                {t('mss.vault.beiFensterwechselHinweis')}
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
            <h2 className="text-sm font-semibold text-on-surface">{t('mss.vault.biometrieTitel')}</h2>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          {!isInitialized ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t('mss.vault.nichtEingerichtet')}
            </div>
          ) : !isUnlocked ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t('mss.vault.gesperrt')}
            </div>
          ) : null}

          <div className="flex items-center justify-between gap-4">
            <div>
              <span className="text-xs font-medium text-on-surface">
                {t('mss.vault.biometrieSchalter')}
              </span>
              <p className="text-[11px] text-on-surface-variant">
                {t('mss.vault.biometrieHinweis')}
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
              {t('mss.vault.biometrieNichtMoeglich')}
            </div>
          )}
        </div>
      </div>

      {/* Biometrie Aktivierungs-Modal */}
      {biometricsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl bg-surface-container border border-outline-variant/30 p-5 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Fingerprint className="h-4 w-4" />
              </div>
              <h3 className="text-sm font-semibold text-on-surface">{t('mss.vault.biometrieEinrichten')}</h3>
            </div>
            <p className="text-xs text-on-surface-variant">
              {t('mss.vault.biometrieBestaetigen')}
            </p>
            <form onSubmit={handleConfirmBiometrics} className="space-y-3">
              <input
                type="password"
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder={t('mss.vault.masterPasswort')}
                className="msm-input"
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
                  {t('common.cancel')}
                </Button>
                <Button
                  type="submit"
                  disabled={biometricsLoading || !masterPasswordInput}
                >
                  {biometricsLoading ? t('mss.vault.biometriePruefe') : t('mss.vault.biometrieAktivieren')}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
