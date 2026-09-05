import { useEffect, useState } from 'react'
import { Copy, MonitorSmartphone, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { API_ORIGIN } from '@/config/api'
import { SecretOnce } from '@/components/ui/SecretOnce'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

interface Geraet {
  family: string
  label: string
  paired_at: string | null
  is_active?: boolean
  last_active_at?: string | null
}

/**
 * Geräte koppeln — der einzige Weg, wie das Smart System hereinkommt.
 *
 * Die Desktop-App meldet sich nicht mit Passwort an. Sie könnte es bei
 * aktiviertem Captcha auch nicht: `/api/auth/login` verlangt dann ein
 * Turnstile-Token, und ein Captcha-Widget in einem Tauri-Fenster scheitert
 * daran, dass Cloudflare-Schlüssel an Domains hängen. Stattdessen lädt hier
 * ein, wer ohnehin schon angemeldet ist.
 *
 * Zwei Dinge stehen deshalb auf dieser Karte, und beide werden gebraucht:
 * der Code und die **API-Adresse**. Wer in der App die Adresse der Oberfläche
 * einträgt, bekommt eine Webseite statt Daten — das ist der häufigste Fehler
 * beim Einrichten, und er lässt sich hier verhindern statt nachher erklären.
 *
 * Der Code selbst geht durch `SecretOnce`: er existiert genau einmal, in
 * dieser Antwort. MSM speichert nur seinen Hash.
 */
export function AiDevicePairingCard() {
  const { t } = useTranslation()
  const [geraete, setGeraete] = useState<Geraet[]>([])
  const [name, setName] = useState('')
  const [code, setCode] = useState<string | null>(null)
  const [qrDataUri, setQrDataUri] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const laden = () => {
    api<Geraet[]>('/auth/devices')
      .then(setGeraete)
      .catch(() => setGeraete([]))
  }

  useEffect(laden, [])

  useEffect(() => {
    if (!code) return
    let aktiv = true
    const interval = setInterval(async () => {
      try {
        const res = await api<{ exists: boolean; redeemed: boolean; expired: boolean; label?: string }>(
          `/auth/devices/pairing/${encodeURIComponent(code)}/status`,
        )
        if (!aktiv) return
        if (res.redeemed) {
          toast.success(t('ai.profile.devicePairSuccess', 'Gerät erfolgreich gekoppelt!'))
          setCode(null)
          setQrDataUri(null)
          laden()
        } else if (res.expired) {
          toast.error(t('ai.profile.devicePairExpired', 'Kopplungscode abgelaufen.'))
          setCode(null)
          setQrDataUri(null)
        }
      } catch {
        // Hintergrund-Prüfung tolerant halten
      }
    }, 2000)

    return () => {
      aktiv = false
      clearInterval(interval)
    }
  }, [code])

  const koppeln = async () => {
    setBusy(true)
    try {
      const antwort = await api<{ code: string; qr_data_uri?: string | null }>('/auth/devices/pairing', {
        method: 'POST',
        body: JSON.stringify({ label: name.trim() }),
      })
      setCode(antwort.code)
      setQrDataUri(antwort.qr_data_uri || null)
      setName('')
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setBusy(false)
    }
  }

  const entziehen = async (geraet: Geraet) => {
    try {
      await api(`/auth/devices/${encodeURIComponent(geraet.family)}`, { method: 'DELETE' })
      toast.success(t('ai.profile.deviceRevoked', 'Zugang entzogen.'))
      laden()
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    }
  }

  const hostOnly = (() => {
    try {
      return API_ORIGIN.replace(/^https:\/\//i, '').replace(/\/+$/, '')
    } catch {
      return API_ORIGIN
    }
  })()

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-devices-title">
      <div className="flex items-center gap-2">
        <MonitorSmartphone className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="ai-devices-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.profile.devicesTitle', 'Geräte koppeln')}
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('ai.profile.devicesDescription')}
      </p>

      {/* Die Adresse steht neben dem Code und nicht in einer Anleitung: sie
          wird im selben Moment gebraucht. */}
      <div className="max-w-xl">
        <label
          htmlFor="mss-api-adresse"
          className="mb-1 block text-xs font-medium text-on-surface-variant"
        >
          {t('ai.profile.devicesApiAddress', 'Diese Adresse in der App eintragen')}
        </label>
        <div className="flex items-center gap-2">
          <input
            id="mss-api-adresse"
            className="msm-input flex-1 cursor-not-allowed opacity-70"
            value={hostOnly}
            readOnly
          />
          <Button
            variant="secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(hostOnly)
              toast.success(t('hoster.copied'))
            }}
          >
            <Copy className="h-4 w-4" aria-hidden="true" />
            {t('common.copy', 'Kopieren')}
          </Button>
        </div>
        <p className="msm-field-help">{t('ai.profile.devicesApiAddressHint')}</p>
      </div>

      {code ? (
        <SecretOnce
          label={t('ai.profile.devicesCodeLabel', 'Kopplungscode')}
          value={code}
          qrDataUri={qrDataUri}
          hinweis={t('ai.profile.devicesOnceHint', 'Gültig für 10 Minuten. Einmalig nutzbar über Code-Eingabe oder QR-Scan.')}
          onDismiss={() => {
            setCode(null)
            setQrDataUri(null)
            laden()
          }}
        />
      ) : (
        <div className="flex max-w-xl items-end gap-3">
          <label className="flex-1">
            <span className="mb-1 block text-xs font-medium text-on-surface-variant">
              {t('ai.profile.devicesNameLabel', 'Name des Geräts')}
            </span>
            <input
              className="msm-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('ai.profile.devicesNamePlaceholder', 'Arbeitsrechner')}
              maxLength={64}
            />
          </label>
          <Button onClick={koppeln} disabled={busy}>
            {t('ai.profile.devicesPair', 'Code erzeugen')}
          </Button>
        </div>
      )}

      {geraete.length > 0 && (
        <ul className="divide-y divide-outline-variant/30 border-t border-outline-variant/30 pt-2">
          {geraete.map((geraet) => (
            <li key={geraet.family} className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 py-3">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="truncate text-sm font-medium text-on-surface">
                    {geraet.label || t('ai.profile.devicesUnnamed', 'Unbenanntes Gerät')}
                  </span>
                  {geraet.is_active !== false ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500 border border-emerald-500/20">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      {t('ai.profile.deviceActive', 'Online')}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-2 py-0.5 text-xs font-medium text-on-surface-variant border border-outline-variant/30">
                      <span className="h-1.5 w-1.5 rounded-full bg-on-surface-variant/50" />
                      {t('ai.profile.deviceInactive', 'Offline')}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-on-surface-variant">
                  {geraet.last_active_at && (
                    <span>
                      {t('ai.profile.deviceLastActive', 'Letzte Aktivität')}:{' '}
                      {(() => {
                        const datum = new Date(geraet.last_active_at)
                        const diffSekunden = Math.floor((Date.now() - datum.getTime()) / 1000)
                        if (diffSekunden < 60) return t('ai.profile.deviceActiveNow', 'Gerade aktiv')
                        const diffMin = Math.floor(diffSekunden / 60)
                        if (diffMin < 60) return t('ai.profile.deviceMinutesAgo', { count: diffMin, defaultValue: `vor ${diffMin} Min.` })
                        const diffStd = Math.floor(diffMin / 60)
                        if (diffStd < 24) return t('ai.profile.deviceHoursAgo', { count: diffStd, defaultValue: `vor ${diffStd} Std.` })
                        return datum.toLocaleString()
                      })()}
                    </span>
                  )}
                  {geraet.paired_at && (
                    <span>
                      {t('ai.profile.devicePairedAt', 'Gekoppelt am')}:{' '}
                      {new Date(geraet.paired_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
              <Button
                variant="secondary"
                onClick={() => void entziehen(geraet)}
                className="self-start sm:self-auto text-error hover:text-error hover:bg-error/10"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('ai.profile.devicesRevoke', 'Zugang entziehen')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
