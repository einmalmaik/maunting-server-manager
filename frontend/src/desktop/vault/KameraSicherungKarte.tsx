/**
 * Kamera-Sicherung in den Einstellungen (Sicherheit → Tresor), nur unter Android.
 *
 * Einschalten geht nur bei offenem Tresor: der Stand liegt je Tresor, und
 * dabei wird der Posteingang eingerichtet, über den bei gesperrtem Tresor
 * gesichert wird (`tresorEingang.ts`). Solange er fehlt, sagt die Karte das.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Camera } from 'lucide-react'
import { useShallow } from 'zustand/react/shallow'

import { Button, Switch } from '@/Singra/UI'
import { medienZugriff } from '@/desktop/tauri'
import { formatBytes, formatZeitpunkt } from '@/lib/format'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import {
  freigebbar,
  kameraAnstossen,
  kameraAusschalten,
  kameraEinschalten,
  kameraNurWlan,
  kameraStandLaden,
  kameraVorhandeneSichern,
  speicherFreigeben,
  useKameraSicherung,
} from './kameraSicherung'
import { useVaultStore } from './vaultStore'

export function KameraSicherungKarte() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { isUnlocked, bucketId } = useVaultStore(useShallow((s) => ({ isUnlocked: s.isUnlocked, bucketId: s.bucketId })))
  const { stand, laeuft, warten } = useKameraSicherung()
  const [beschaeftigt, setBeschaeftigt] = useState(false)
  const [papierkorb, setPapierkorb] = useState(false)

  const bucket = isUnlocked ? bucketId : null

  useEffect(() => {
    if (!bucket) return
    void kameraStandLaden(bucket)
    medienZugriff(false)
      .then((z) => setPapierkorb(z.papierkorb))
      .catch(() => setPapierkorb(false))
  }, [bucket])

  const an = !!bucket && !!stand?.an

  const umschalten = async (wert: boolean) => {
    if (!bucket) return
    setBeschaeftigt(true)
    try {
      if (!wert) {
        await kameraAusschalten(bucket)
        return
      }
      const zugriff = await kameraEinschalten(bucket)
      if (zugriff.stand !== 'voll') toast.error(t(`mss.vault.kamera.zugriff.${zugriff.stand}`))
    } catch {
      toast.error(t('mss.vault.kamera.fehler'))
    } finally {
      setBeschaeftigt(false)
    }
  }

  const zugriffErteilen = async () => {
    if (!bucket) return
    const zugriff = await medienZugriff(true).catch(() => null)
    if (zugriff?.stand === 'voll') void kameraAnstossen(bucket)
    else toast.error(t(`mss.vault.kamera.zugriff.${zugriff?.stand ?? 'keiner'}`))
  }

  const vorhandene = async () => {
    if (!bucket) return
    const ok = await confirm({
      title: t('mss.vault.kamera.vorhandeneTitel'),
      message: t('mss.vault.kamera.vorhandeneFrage'),
      confirmText: t('mss.vault.kamera.vorhandeneKnopf'),
    })
    if (ok) await kameraVorhandeneSichern(bucket)
  }

  const freigeben = async () => {
    if (!bucket) return
    setBeschaeftigt(true)
    try {
      const freigabe = await freigebbar(bucket)
      const anzahl = freigabe.bilder.length + freigabe.videos.length
      if (anzahl === 0) {
        toast.info(t('mss.vault.kamera.nichtsFreizugeben'))
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
            <p className="text-label-sm text-on-surface-variant">{t('mss.vault.kamera.hinweis')}</p>
          </div>
          <Switch
            aria-labelledby="kamera-sicherung"
            checked={an}
            disabled={!bucket || beschaeftigt}
            onCheckedChange={(wert: boolean) => void umschalten(wert)}
          />
        </div>

        {an && stand && (
          <>
            <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
              <span id="kamera-wlan" className="text-xs font-medium text-on-surface">
                {t('mss.vault.kamera.nurWlan')}
              </span>
              <Switch
                aria-labelledby="kamera-wlan"
                checked={stand.nurWlan}
                onCheckedChange={(wert: boolean) => void kameraNurWlan(stand.bucket, wert)}
              />
            </div>

            <p className="text-label-sm text-on-surface-variant" aria-live="polite">
              {laeuft
                ? t('mss.vault.kamera.laeuft')
                : stand.zuletzt
                  ? t('mss.vault.kamera.stand', {
                      count: stand.gesichert,
                      zeit: formatZeitpunkt(stand.zuletzt, i18n.language),
                    })
                  : t('mss.vault.kamera.nochNichts')}
            </p>

            {!stand.eingang && (
              <p className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
                {t('mss.vault.kamera.eingangFehlt')}
              </p>
            )}

            {warten && (
              <div className="flex flex-col items-start gap-2 p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant sm:flex-row sm:items-center sm:gap-3">
                <span className="flex-1">{t(`mss.vault.kamera.warten.${warten}`)}</span>
                {warten === 'zugriff' && (
                  <Button variant="secondary" size="sm" type="button" onClick={() => void zugriffErteilen()}>
                    {t('mss.vault.kamera.zugriffErteilen')}
                  </Button>
                )}
                {warten === 'fehler' && (
                  <Button variant="secondary" size="sm" type="button" onClick={() => void kameraAnstossen(stand.bucket)}>
                    {t('mss.vault.kamera.nochmal')}
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
                  disabled={beschaeftigt || laeuft}
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
