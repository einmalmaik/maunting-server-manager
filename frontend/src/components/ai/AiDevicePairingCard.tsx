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
  const [busy, setBusy] = useState(false)

  const laden = () => {
    api<Geraet[]>('/auth/devices')
      .then(setGeraete)
      .catch(() => setGeraete([]))
  }

  useEffect(laden, [])

  const koppeln = async () => {
    setBusy(true)
    try {
      const antwort = await api<{ code: string }>('/auth/devices/pairing', {
        method: 'POST',
        body: JSON.stringify({ label: name.trim() }),
      })
      setCode(antwort.code)
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
            value={API_ORIGIN}
            readOnly
          />
          <Button
            variant="secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(API_ORIGIN)
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
          onDismiss={() => {
            setCode(null)
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
            <li key={geraet.family} className="flex items-center justify-between gap-4 py-3">
              <span className="min-w-0">
                <span className="block truncate text-sm text-on-surface">
                  {geraet.label || t('ai.profile.devicesUnnamed', 'Unbenanntes Gerät')}
                </span>
                {geraet.paired_at && (
                  <span className="block text-xs text-on-surface-variant">
                    {new Date(geraet.paired_at).toLocaleString()}
                  </span>
                )}
              </span>
              <Button variant="secondary" onClick={() => void entziehen(geraet)}>
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
