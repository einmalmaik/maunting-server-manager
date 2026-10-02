import { blobHerunterladen } from '@/lib/herunterladen'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Download, Fingerprint, QrCode, RotateCcw, Shield, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { Badge, Button, buttonClasses, Input, MauntingQrCard } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Spinner } from '@/components/ui/Spinner'
import { useZweitfaktor, ZweitfaktorFeld } from '@/components/auth/ZweitfaktorNachweis'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { passkeyAnlegen, webauthnVerfuegbar, type PasskeyAnlageOptionen } from '@/services/passkeyService'
import { formatRelativeTime } from '@/utils/timeFormat'
import type { User } from '@/types'

interface PasskeyEintrag {
  id: number
  name: string | null
  created_at: string | null
  last_used_at: string | null
}

interface Einrichtung {
  secret: string
  uri: string
  qr_data_uri: string | null
}

/**
 * Tab: Zwei-Faktor-Authentifizierung.
 *
 * Seit 29.09.2026 gelten beliebig viele Passkeys und die Authenticator-App
 * nebeneinander. Vorher hatte ein Konto genau einen Faktor, und wer den
 * einzigen Passkey am PC angelegt hatte, kam am neuen Handy nur per
 * Backup-Code hinein.
 *
 * Jede Änderung braucht einen Nachweis, der oben in der Karte steht: bei
 * aktiver 2FA ein Faktor, sonst das Passwort. Ein Konto nur mit Social Login
 * hat kein Passwort und richtet den ersten Faktor ohne Nachweis ein.
 */
export function TwoFactorTab() {
  const { t } = useTranslation()
  const { user, setUser } = useAuthStore()
  const faktor = useZweitfaktor()
  const an = Boolean(user?.two_factor_enabled)
  const appAktiv = faktor.methoden.includes('totp')
  const brauchtPasswort = !an && user?.has_password !== false

  const [passwort, setPasswort] = useState('')
  const [passkeys, setPasskeys] = useState<PasskeyEintrag[]>([])
  const [name, setName] = useState('')
  const [einrichtung, setEinrichtung] = useState<Einrichtung | null>(null)
  const [neuerCode, setNeuerCode] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[]>([])
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState('')
  const [meldung, setMeldung] = useState('')

  const kannBestaetigen = an ? faktor.bereit : !brauchtPasswort || passwort.length > 0
  const gesperrt = laeuft || !kannBestaetigen

  const passkeysLaden = useCallback(async () => {
    try {
      setPasskeys(await api<PasskeyEintrag[]>('/auth/2fa/passkeys'))
    } catch {
      // Die Liste bleibt, wie sie war. Die Aktionen melden ihre Fehler selbst.
    }
  }, [])

  useEffect(() => {
    void passkeysLaden()
  }, [passkeysLaden])

  const ausfuehren = async (arbeit: () => Promise<string | void>) => {
    setFehler('')
    setMeldung('')
    setLaeuft(true)
    try {
      const text = await arbeit()
      if (text) setMeldung(text)
    } catch (err) {
      setFehler(err instanceof Error ? err.message : String(err))
    } finally {
      setLaeuft(false)
    }
  }

  /** Nachweis vor einer Änderung: ein Faktor, ohne 2FA das Passwort. */
  const nachweis = async () => {
    if (an) return faktor.nachweis('2fa_change')
    return brauchtPasswort ? { password: passwort } : {}
  }

  const neuLaden = async () => {
    setUser(await api<User>('/auth/me'))
    await passkeysLaden()
    setPasswort('')
    faktor.setCode('')
  }

  const letzterFaktorBestaetigt = () =>
    confirm({
      title: t('profile.zweitfaktoren.letzterTitel'),
      message: t('profile.zweitfaktoren.letzterText'),
      confirmText: t('profile.zweitfaktoren.entfernen'),
      danger: true,
    })

  const passkeyHinzufuegen = () =>
    ausfuehren(async () => {
      const optionen = await api<PasskeyAnlageOptionen>('/auth/2fa/passkey/options', {
        method: 'POST',
        body: JSON.stringify(await nachweis()),
      })
      const anlage = await passkeyAnlegen(optionen)
      const res = await api<{ backup_codes?: string[] }>('/auth/2fa/passkey/enable', {
        method: 'POST',
        body: JSON.stringify({ ...anlage, name: name.trim() || null }),
      })
      if (res.backup_codes) setBackupCodes(res.backup_codes)
      setName('')
      await neuLaden()
      return t('profile.zweitfaktoren.passkeyGespeichert')
    })

  const passkeyEntfernen = (eintrag: PasskeyEintrag) =>
    ausfuehren(async () => {
      if (passkeys.length === 1 && !appAktiv && !(await letzterFaktorBestaetigt())) return
      await api(`/auth/2fa/passkeys/${eintrag.id}/remove`, {
        method: 'POST',
        body: JSON.stringify(await faktor.nachweis('2fa_change')),
      })
      await neuLaden()
      return t('profile.zweitfaktoren.passkeyEntfernt')
    })

  const appEinrichten = () =>
    ausfuehren(async () => {
      setEinrichtung(
        await api<Einrichtung>('/auth/2fa/setup', { method: 'POST', body: JSON.stringify(await nachweis()) }),
      )
    })

  const appBestaetigen = (e: React.FormEvent) => {
    e.preventDefault()
    void ausfuehren(async () => {
      const res = await api<{ backup_codes?: string[] }>(
        '/auth/2fa/enable?otp_code=' + encodeURIComponent(neuerCode),
        { method: 'POST' },
      )
      if (res.backup_codes) setBackupCodes(res.backup_codes)
      // Das Geheimnis hat in der Oberfläche danach nichts mehr verloren.
      setEinrichtung(null)
      setNeuerCode('')
      await neuLaden()
      return t('profile.zweitfaktoren.appGespeichert')
    })
  }

  const appEntfernen = () =>
    ausfuehren(async () => {
      if (passkeys.length === 0 && !(await letzterFaktorBestaetigt())) return
      await api('/auth/2fa/totp/remove', {
        method: 'POST',
        body: JSON.stringify(await faktor.nachweis('2fa_change')),
      })
      await neuLaden()
      return t('profile.zweitfaktoren.appEntfernt')
    })

  const backupCodesNeu = () =>
    ausfuehren(async () => {
      const res = await api<{ codes: string[] }>('/auth/2fa/backup/generate', {
        method: 'POST',
        body: JSON.stringify(await faktor.nachweis('2fa_change')),
      })
      setBackupCodes(res.codes)
      faktor.setCode('')
      return t('profile.backupCodesRegenerated')
    })

  const ausschalten = () =>
    ausfuehren(async () => {
      const ok = await confirm({
        title: t('profile.zweitfaktoren.ausschaltenTitel'),
        message: t('profile.zweitfaktoren.ausschaltenText'),
        confirmText: t('profile.2faDisable'),
        danger: true,
      })
      if (!ok) return
      const n = await faktor.nachweis('2fa_disable')
      const query = 'otp_code' in n ? '?otp_code=' + encodeURIComponent(n.otp_code) : ''
      await api('/auth/2fa/disable' + query, {
        method: 'POST',
        body: JSON.stringify('passkey' in n ? { passkey: n.passkey } : {}),
      })
      setBackupCodes([])
      await neuLaden()
      return t('profile.2faDisabled')
    })

  const handleDownloadBackupCodes = () => {
    if (backupCodes.length === 0) return
    const blob = new Blob([backupCodes.join('\n')], { type: 'text/plain;charset=utf-8' })
    blobHerunterladen(blob, `msm-backup-codes-${new Date().toISOString().slice(0, 10)}.txt`)
    setBackupCodes([])
    setMeldung(t('profile.backupCodesDownloaded'))
  }

  const zuletzt = (eintrag: PasskeyEintrag) =>
    eintrag.last_used_at
      ? t('profile.zweitfaktoren.passkeyZuletzt', { wann: formatRelativeTime(eintrag.last_used_at, t) })
      : t('profile.zweitfaktoren.passkeyNie')

  return (
    <div className="msm-card p-6">
      <div className="flex items-center gap-2 mb-6">
        <Shield className="h-5 w-5 text-secondary" aria-hidden="true" />
        <div className="flex-1">
          <h2 className="font-headline text-title-lg font-semibold text-on-surface">{t('profile.2faStatus')}</h2>
        </div>
        <Badge variant={an ? 'success' : 'destructive'}>{an ? t('profile.2faEnabled') : t('profile.2faDisabled')}</Badge>
      </div>

      {an && faktor.methoden.length === 1 && passkeys.length <= 1 && (
        <p className="mb-4 flex items-start gap-2 rounded-lg bg-status-warning/10 px-3 py-2 text-xs text-on-surface-variant">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-warning" aria-hidden="true" />
          <span>{t('profile.zweitfaktoren.nurEinFaktor')}</span>
        </p>
      )}

      {(an || brauchtPasswort) && (
        <div className="mb-6 max-w-md space-y-2">
          <p className="text-sm font-medium text-on-surface">{t('profile.zweitfaktoren.nachweisTitel')}</p>
          {an ? (
            <ZweitfaktorFeld faktor={faktor} id="zweitfaktor-nachweis" disabled={laeuft} />
          ) : (
            <PasswordInput
              id="zweitfaktor-passwort"
              label={t('profile.currentPassword')}
              value={passwort}
              onChange={(e) => setPasswort(e.target.value)}
              autoComplete="current-password"
              disabled={laeuft}
            />
          )}
        </div>
      )}

      {fehler && <div className="msm-alert-error text-sm mb-4" role="alert">{fehler}</div>}
      {meldung && <div className="msm-alert-success text-sm mb-4" role="status">{meldung}</div>}

      <section className="space-y-3" aria-labelledby="passkeys-titel">
        <div className="flex items-center gap-2">
          <Fingerprint className="h-4 w-4 text-primary" aria-hidden="true" />
          <h3 id="passkeys-titel" className="text-base font-semibold text-on-surface">
            {t('profile.zweitfaktoren.passkeysTitel')}
          </h3>
        </div>
        {passkeys.length === 0 ? (
          <p className="text-sm text-on-surface-variant">{t('profile.zweitfaktoren.passkeysLeer')}</p>
        ) : (
          <ul className="divide-y divide-outline-variant/30 border-t border-outline-variant/30">
            {passkeys.map((eintrag) => (
              <li key={eintrag.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="truncate text-sm font-medium text-on-surface">
                    {eintrag.name || t('profile.zweitfaktoren.passkeyOhneName')}
                  </p>
                  <p className="text-xs text-on-surface-variant">
                    {eintrag.created_at &&
                      t('profile.zweitfaktoren.passkeyAngelegt', {
                        datum: new Date(eintrag.created_at).toLocaleDateString(),
                      })}
                    {' · '}
                    {zuletzt(eintrag)}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void passkeyEntfernen(eintrag)}
                  disabled={gesperrt}
                  aria-label={t('profile.zweitfaktoren.passkeyEntfernen', {
                    name: eintrag.name || t('profile.zweitfaktoren.passkeyOhneName'),
                  })}
                  className="text-error hover:bg-error/10 hover:text-error"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                  {t('profile.zweitfaktoren.entfernen')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex max-w-xl flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1">
            <Input
              id="passkey-name"
              label={t('profile.zweitfaktoren.passkeyName')}
              placeholder={t('profile.zweitfaktoren.passkeyNamePlatzhalter')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={64}
              disabled={laeuft}
            />
          </div>
          <Button onClick={() => void passkeyHinzufuegen()} disabled={gesperrt || !webauthnVerfuegbar()}>
            {laeuft ? <Spinner /> : <Fingerprint className="h-4 w-4" aria-hidden="true" />}
            {t('profile.zweitfaktoren.passkeyHinzufuegen')}
          </Button>
        </div>
        <p className="msm-field-help">{t('profile.zweitfaktoren.passkeyHinzufuegenHinweis')}</p>
      </section>

      <section className="mt-6 space-y-3 border-t border-outline-variant/30 pt-6" aria-labelledby="app-titel">
        <div className="flex items-center gap-2">
          <QrCode className="h-4 w-4 text-primary" aria-hidden="true" />
          <h3 id="app-titel" className="text-base font-semibold text-on-surface">
            {t('profile.zweitfaktoren.appTitel')}
          </h3>
          <Badge variant={appAktiv ? 'success' : 'default'}>
            {appAktiv ? t('profile.zweitfaktoren.appAktiv') : t('profile.zweitfaktoren.appInaktiv')}
          </Badge>
        </div>
        {appAktiv ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void appEntfernen()}
            disabled={gesperrt}
            className="text-error hover:bg-error/10 hover:text-error"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {t('profile.zweitfaktoren.appEntfernen')}
          </Button>
        ) : !einrichtung ? (
          <Button variant="secondary" onClick={() => void appEinrichten()} disabled={gesperrt}>
            <QrCode className="h-4 w-4" aria-hidden="true" />
            {t('profile.zweitfaktoren.appEinrichten')}
          </Button>
        ) : (
          <div className="space-y-4">
            <p className="font-body-md text-sm text-on-surface-variant text-center">{t('profile.2faScan')}</p>
            <div className="flex flex-col items-center gap-4">
              {einrichtung.qr_data_uri && (
                <MauntingQrCard
                  value={einrichtung.uri}
                  qrDataUri={einrichtung.qr_data_uri}
                  alt={t('profile.2faQrCode')}
                  hint={t('profile.2faScanHint')}
                />
              )}
              <p className="font-mono-sm text-mono-sm text-on-surface-variant bg-surface-container-high px-3.5 py-1.5 rounded-lg border border-outline-variant select-all tracking-wider font-bold">
                {einrichtung.secret}
              </p>
              <a href={einrichtung.uri} className={buttonClasses('secondary')}>
                <Shield className="h-4 w-4" aria-hidden="true" />
                {t('profile.2faOpenApp')}
              </a>
            </div>
            <form onSubmit={appBestaetigen} className="mx-auto flex max-w-xs flex-col gap-3">
              <Input
                id="app-neuer-code"
                label={t('profile.2faEnterCode')}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={neuerCode}
                onChange={(e) => setNeuerCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                className="text-center text-xl tracking-[0.5em] font-mono"
                placeholder="000000"
                required
              />
              <Button type="submit" disabled={laeuft || neuerCode.length !== 6}>
                {laeuft ? <Spinner /> : t('common.save')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEinrichtung(null)
                  setNeuerCode('')
                }}
              >
                {t('common.cancel')}
              </Button>
            </form>
          </div>
        )}
      </section>

      {an && (
        <section className="mt-6 space-y-3 border-t border-outline-variant/30 pt-6" aria-labelledby="backup-titel">
          <h3 id="backup-titel" className="text-base font-semibold text-on-surface">
            {t('profile.backupCodes')}
          </h3>
          <p className="text-sm text-on-surface-variant">{t('profile.zweitfaktoren.backupHinweis')}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="secondary" onClick={() => void backupCodesNeu()} disabled={gesperrt}>
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {t('profile.regenerateBackupCodes')}
            </Button>
            <Button variant="destructive" onClick={() => void ausschalten()} disabled={gesperrt}>
              {t('profile.2faDisable')}
            </Button>
          </div>
        </section>
      )}

      {backupCodes.length > 0 && (
        <div className="mt-4 p-4 bg-status-warning/5 border border-status-warning/20 rounded-lg">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-status-warning" aria-hidden="true" />
            <p className="font-label-md text-sm text-status-warning font-medium">{t('profile.backupCodes')}</p>
          </div>
          <p className="font-body-md text-xs text-on-surface-variant mb-3">{t('profile.backupCodesWarning')}</p>
          <p className="font-body-md text-xs text-on-surface-variant mb-3">{t('profile.backupCodesDownloadOnce')}</p>
          <Button type="button" onClick={handleDownloadBackupCodes}>
            <Download className="w-4 h-4" aria-hidden="true" />
            {t('profile.downloadBackupCodes')}
          </Button>
        </div>
      )}
    </div>
  )
}
