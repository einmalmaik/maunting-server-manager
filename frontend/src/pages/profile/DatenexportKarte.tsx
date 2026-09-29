import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, KeyRound, MessageSquareLock } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Spinner } from '@/components/ui/Spinner'
import { useAuthStore } from '@/stores/authStore'
import { useZweitfaktor, ZweitfaktorFeld } from '@/components/auth/ZweitfaktorNachweis'
import { istOffen } from '@/services/lokaleVersiegelung'
import type { ExportErgebnis, TresorQuelle } from '@/services/datenexport'

interface Props {
  /** Nur in der App: der Tresor geht mit, wenn er entsperrt ist. */
  tresor?: TresorQuelle & { entsperren: () => void }
}

/**
 * „Meine Daten exportieren“: Nachweis und Knopf in einem Schritt, ohne Dialog.
 *
 * Der Nachweis ist der, den das Konto hat: bei 2FA ein eingerichteter Faktor,
 * sonst das Passwort. Ein Social-Konto ohne beides exportiert ohne Nachweis,
 * dann fehlen nur die hinterlegten Zugangsdaten (Backend entscheidet).
 */
export function DatenexportKarte({ tresor }: Props) {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const faktor = useZweitfaktor()
  const brauchtPasswort = !faktor.wahl && user?.has_password !== false

  const [passwort, setPasswort] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState('')
  const [ergebnis, setErgebnis] = useState<ExportErgebnis | null>(null)
  const messengerOffen = istOffen()

  const exportieren = async (e: React.FormEvent) => {
    e.preventDefault()
    setFehler('')
    setErgebnis(null)
    setLaeuft(true)
    try {
      // Der Passkey zuerst: der Browser verlangt die Abfrage nah am Klick.
      const zweiter = await faktor.nachweis('data_export')
      const { exportErstellen, exportSpeichern } = await import('@/services/datenexport')
      const fertig = await exportErstellen(
        { password: brauchtPasswort ? passwort : '', ...zweiter },
        tresor,
      )
      if (await exportSpeichern(fertig.blob, fertig.dateiname)) {
        setErgebnis(fertig)
        setPasswort('')
        faktor.setCode('')
      }
    } catch (err) {
      setFehler(err instanceof Error ? err.message : String(err))
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <section className="msm-card p-6" aria-labelledby="data-export-title">
      <div className="flex items-center gap-2 mb-2">
        <Download className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 id="data-export-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('profile.dataExport.title')}
        </h2>
      </div>
      <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-4">
        {t('profile.dataExport.description')}
      </p>

      {!messengerOffen && (
        <p className="mb-3 flex items-start gap-2 rounded-lg bg-surface-container-high/60 px-3 py-2 text-xs text-on-surface-variant">
          <MessageSquareLock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          <span>{t('profile.dataExport.messengerLocked')}</span>
        </p>
      )}
      {tresor && !tresor.entsperrt && (
        <div className="mb-3 flex flex-col items-start gap-2 rounded-lg bg-surface-container-high/60 px-3 py-2 text-xs text-on-surface-variant sm:flex-row sm:items-center sm:gap-3">
          <span className="flex flex-1 items-start gap-2">
            <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
            {t('profile.dataExport.vaultLocked')}
          </span>
          <Button variant="secondary" size="sm" type="button" onClick={tresor.entsperren}>
            {t('profile.dataExport.vaultUnlock')}
          </Button>
        </div>
      )}

      <form onSubmit={exportieren} className="max-w-md space-y-4">
        {brauchtPasswort && (
          <PasswordInput
            label={t('profile.dataExport.passwordLabel')}
            value={passwort}
            onChange={(e) => setPasswort(e.target.value)}
            autoComplete="current-password"
            required
            disabled={laeuft}
          />
        )}
        <ZweitfaktorFeld
          faktor={faktor}
          id="data-export-otp"
          label={t('profile.dataExport.otpLabel')}
          disabled={laeuft}
        />
        {!brauchtPasswort && !faktor.wahl && (
          <p className="text-xs text-on-surface-variant">{t('profile.dataExport.withoutSecrets')}</p>
        )}

        {fehler && <div className="msm-alert-error text-sm" role="alert">{fehler}</div>}

        <Button type="submit" disabled={laeuft} className="inline-flex items-center gap-2">
          {laeuft ? <Spinner /> : <Download className="h-4 w-4" aria-hidden="true" />}
          {laeuft ? t('profile.dataExport.running') : t('profile.dataExport.button')}
        </Button>
      </form>

      {ergebnis && (
        <div className="mt-4 space-y-1 text-sm text-on-surface-variant" role="status">
          <p className="text-on-surface">{t('profile.dataExport.done', { datei: ergebnis.dateiname })}</p>
          {ergebnis.ohneSchluessel > 0 && <p>{t('profile.dataExport.notesLocked', { count: ergebnis.ohneSchluessel })}</p>}
          {ergebnis.messengerGesperrt && <p>{t('profile.dataExport.messengerMissing')}</p>}
          {ergebnis.tresor === 'gesperrt' && <p>{t('profile.dataExport.vaultMissing')}</p>}
        </div>
      )}
    </section>
  )
}
