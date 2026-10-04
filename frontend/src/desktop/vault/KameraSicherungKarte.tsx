/**
 * Kamera-Sicherung in den Einstellungen (Sicherheit → Tresor), nur unter Android.
 *
 * Einschalten geht nur bei offenem Tresor und mit frischem Nachweis: das
 * Telefon bekommt einen Zugang, mit dem es auch ohne offene App hochladen
 * darf (AGENTS.md Punkt 21). Der Nachweis steht in der Karte, kein eigener
 * Dialog. Fällt der Zugang weg (Abmelden, Sitzung entzogen), fragt die Karte
 * an derselben Stelle neu.
 *
 * Offline steht der Hauptschalter still: Einschalten braucht den Nachweis beim
 * Server, und Ausschalten ließe den Zugang dort stehen, während das Telefon ihn
 * schon vergessen hat. WLAN, Bildschirmfotos und „Vorhandene sichern“ bleiben
 * Sache des Telefons.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Camera } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'

import { Button, Switch } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { useZweitfaktor, ZweitfaktorFeld } from '@/components/auth/ZweitfaktorNachweis'
import { medienEinstellungen, medienZugriff } from '@/desktop/tauri'
import { formatBytes, formatZeitpunkt } from '@/lib/format'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import {
  freigebbar,
  kameraAusschalten,
  kameraEinschalten,
  kameraNurWlan,
  kameraScreenshots,
  kameraStandLaden,
  kameraVorhandeneSichern,
  speicherFreigeben,
  useKameraSicherung,
} from './kameraSicherung'
import { useVaultStore } from './vaultStore'

export function KameraSicherungKarte({ offline = false }: { offline?: boolean }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { isUnlocked, bucketId } = useVaultStore(useShallow((s) => ({ isUnlocked: s.isUnlocked, bucketId: s.bucketId })))
  const brauchtPasswort = useAuthStore((s) => s.user?.has_password !== false)
  const stand = useKameraSicherung((s) => s.stand)
  const faktor = useZweitfaktor()
  const [beschaeftigt, setBeschaeftigt] = useState(false)
  const [papierkorb, setPapierkorb] = useState(false)
  const [nachweisOffen, setNachweisOffen] = useState(false)
  const [passwort, setPasswort] = useState('')

  const bucket = isUnlocked ? bucketId : null
  // Eingerichtet für einen anderen Tresor zählt hier nicht als an.
  const aktiv = bucket && stand?.eingerichtet && stand.bucket === bucket ? stand : null
  const zugangFehlt = aktiv?.warten === 'zugang'

  useEffect(() => {
    if (!bucket) return
    void kameraStandLaden()
    medienZugriff(false)
      .then((z) => setPapierkorb(z.papierkorb))
      .catch(() => setPapierkorb(false))
  }, [bucket])

  const umschalten = async (wert: boolean) => {
    if (!wert) {
      setNachweisOffen(false)
      setBeschaeftigt(true)
      try {
        await kameraAusschalten()
      } catch {
        toast.error(t('mss.vault.kamera.fehler'))
      } finally {
        setBeschaeftigt(false)
      }
      return
    }
    setNachweisOffen(true)
  }

  const einschalten = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!bucket) return
    setBeschaeftigt(true)
    try {
      // Der Passkey zuerst: die Abfrage muss nah am Klick liegen.
      const zweiter = await faktor.nachweis('kamera_sicherung')
      const ergebnis = await kameraEinschalten(bucket, { password: brauchtPasswort ? passwort : null, ...zweiter })
      if (ergebnis === 'ok') {
        setNachweisOffen(false)
        setPasswort('')
        faktor.setCode('')
      } else if (ergebnis === 'eingang') {
        toast.error(t('mss.vault.kamera.eingangFehlt'))
      } else {
        toast.error(t(`mss.vault.kamera.zugriff.${ergebnis}`))
      }
    } catch (err) {
      toast.error(err instanceof Error && err.message ? err.message : t('mss.vault.kamera.fehler'))
    } finally {
      setBeschaeftigt(false)
    }
  }

  const zugriffErteilen = async () => {
    const zugriff = await medienZugriff(true).catch(() => null)
    if (zugriff?.stand === 'voll') void kameraStandLaden()
    else toast.error(t(`mss.vault.kamera.zugriff.${zugriff?.stand ?? 'keiner'}`))
  }

  const vorhandene = async () => {
    if (!aktiv) return
    const ok = await confirm({
      title: t('mss.vault.kamera.vorhandeneTitel'),
      message: t('mss.vault.kamera.vorhandeneFrage'),
      confirmText: t('mss.vault.kamera.vorhandeneKnopf'),
    })
    if (!ok) return
    await kameraVorhandeneSichern().catch(() => toast.error(t('mss.vault.kamera.fehler')))
  }

  const freigeben = async () => {
    if (!bucket || !aktiv) return
    setBeschaeftigt(true)
    try {
      const freigabe = await freigebbar(bucket)
      const anzahl = freigabe.bilder.length + freigabe.videos.length
      if (anzahl === 0) {
        toast.info(t(freigabe.unerreichbar ? 'mss.vault.kamera.freigebenUnerreichbar' : 'mss.vault.kamera.nichtsFreizugeben'))
        return
      }
      const ok = await confirm({
        title: t('mss.vault.kamera.freigebenTitel'),
        message: t('mss.vault.kamera.freigebenFrage', { count: anzahl, groesse: formatBytes(freigabe.bytes) }),
        confirmText: t('mss.vault.kamera.freigebenKnopf'),
        danger: true,
      })
      if (!ok) return
      if (await speicherFreigeben(freigabe)) toast.success(t('mss.vault.kamera.freigegeben', { count: anzahl }))
    } catch {
      toast.error(t('mss.vault.kamera.fehler'))
    } finally {
      setBeschaeftigt(false)
    }
  }

  const nachweisZeigen = !!bucket && !offline && (nachweisOffen || zugangFehlt)

  return (
    <div className="msm-card p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Camera className="h-5 w-5" />
        </div>
        <h2 className="text-sm font-semibold text-on-surface">{t('mss.vault.kamera.titel')}</h2>
      </div>

      <div className="pt-2 border-t border-outline-variant/30 space-y-3">
        {!bucket && (
          <div className="flex flex-col items-start gap-2 p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant sm:flex-row sm:items-center sm:gap-3">
            <span className="flex-1">{t('mss.vault.kamera.gesperrt')}</span>
            <Button variant="secondary" size="sm" type="button" onClick={() => navigate('/tresor')}>
              {t('profile.dataExport.vaultUnlock')}
            </Button>
          </div>
        )}

        <div className="flex items-center justify-between gap-4">
          <div>
            <span id="kamera-sicherung" className="text-xs font-medium text-on-surface">
              {t('mss.vault.kamera.schalter')}
            </span>
            <p className="text-label-sm text-on-surface-variant">
              {offline ? t('mss.einstellungen.offline.titel') : t('mss.vault.kamera.hinweis')}
            </p>
          </div>
          <Switch
            aria-labelledby="kamera-sicherung"
            checked={!!aktiv || (nachweisOffen && !offline)}
            disabled={!bucket || beschaeftigt || offline}
            onCheckedChange={(wert: boolean) => void umschalten(wert)}
          />
        </div>

        {nachweisZeigen && (
          <form onSubmit={einschalten} className="space-y-3 p-3 rounded-xl bg-surface-container-high border border-outline-variant/30">
            <p className="text-xs text-on-surface-variant">
              {zugangFehlt ? t('mss.vault.kamera.warten.zugang') : t('mss.vault.kamera.nachweisText')}
            </p>
            {brauchtPasswort && (
              <PasswordInput
                id="kamera-passwort"
                label={t('mss.vault.kamera.passwortLabel')}
                value={passwort}
                onChange={(e) => setPasswort(e.target.value)}
                autoComplete="current-password"
                required
                disabled={beschaeftigt}
              />
            )}
            <ZweitfaktorFeld faktor={faktor} id="kamera-otp" label={t('mss.vault.kamera.otpLabel')} disabled={beschaeftigt} />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" className="max-sm:min-h-11" disabled={beschaeftigt || !faktor.bereit}>
                {zugangFehlt ? t('mss.vault.kamera.neuBestaetigen') : t('mss.vault.kamera.einschalten')}
              </Button>
              {nachweisOffen && !zugangFehlt && (
                <Button type="button" variant="ghost" size="sm" className="max-sm:min-h-11" disabled={beschaeftigt} onClick={() => setNachweisOffen(false)}>
                  {t('common.cancel')}
                </Button>
              )}
            </div>
          </form>
        )}

        {aktiv && (
          <>
            <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
              <span id="kamera-wlan" className="text-xs font-medium text-on-surface">
                {t('mss.vault.kamera.nurWlan')}
              </span>
              <Switch
                aria-labelledby="kamera-wlan"
                checked={aktiv.nurWlan}
                disabled={beschaeftigt}
                onCheckedChange={(wert: boolean) => void kameraNurWlan(wert).catch(() => toast.error(t('mss.vault.kamera.fehler')))}
              />
            </div>

            <div className="flex items-center justify-between gap-4">
              <div>
                <span id="kamera-screenshots" className="text-xs font-medium text-on-surface">
                  {t('mss.vault.kamera.screenshots')}
                </span>
                <p className="text-label-sm text-on-surface-variant">{t('mss.vault.kamera.screenshotsHinweis')}</p>
              </div>
              <Switch
                aria-labelledby="kamera-screenshots"
                checked={aktiv.screenshots}
                disabled={beschaeftigt}
                onCheckedChange={(wert: boolean) => void kameraScreenshots(wert).catch(() => toast.error(t('mss.vault.kamera.fehler')))}
              />
            </div>

            <p className="text-label-sm text-on-surface-variant" aria-live="polite">
              {aktiv.zuletzt
                ? t('mss.vault.kamera.stand', { count: aktiv.gesichert, zeit: formatZeitpunkt(aktiv.zuletzt, i18n.language) })
                : t('mss.vault.kamera.nochNichts')}
              {aktiv.offen > 0 && <> {t('mss.vault.kamera.offen', { count: aktiv.offen })}</>}
            </p>

            {aktiv.warten && aktiv.warten !== 'zugang' && (
              <div className="flex flex-col items-start gap-2 p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant sm:flex-row sm:items-center sm:gap-3">
                <span className="flex-1">{t(`mss.vault.kamera.warten.${aktiv.warten}`)}</span>
                {aktiv.warten === 'zugriff' && (
                  <Button variant="secondary" size="sm" type="button" onClick={() => void zugriffErteilen()}>
                    {t('mss.vault.kamera.zugriffErteilen')}
                  </Button>
                )}
                {aktiv.warten === 'akku' && (
                  <Button variant="secondary" size="sm" type="button" onClick={() => void medienEinstellungen().catch(() => toast.error(t('mss.vault.kamera.fehler')))}>
                    {t('mss.vault.kamera.einstellungenOeffnen')}
                  </Button>
                )}
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-2 border-t border-outline-variant/20">
              <Button variant="secondary" size="sm" type="button" className="max-sm:min-h-11" onClick={() => void vorhandene()}>
                {t('mss.vault.kamera.vorhandeneKnopf')}
              </Button>
              {papierkorb && (
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  className="max-sm:min-h-11"
                  disabled={beschaeftigt}
                  onClick={() => void freigeben()}
                >
                  {beschaeftigt ? t('mss.vault.kamera.wirdGeprueft') : t('mss.vault.kamera.freigebenKnopf')}
                </Button>
              )}
            </div>
            {!papierkorb && <p className="text-label-sm text-on-surface-variant">{t('mss.vault.kamera.freigebenAlt')}</p>}
          </>
        )}
      </div>
    </div>
  )
}
