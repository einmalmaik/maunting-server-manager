import { useEffect, useState } from 'react'
import { Copy, MonitorSmartphone, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { formatRelativeTime } from '@/utils/timeFormat'

import { api } from '@/api/client'
import { getE2eeGeraete } from '@/api/social'
import { API_ORIGIN } from '@/config/api'
import { SecretOnce } from '@/components/ui/SecretOnce'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { Button } from '@/Singra/UI'
import { entferneGeraet, gebeGeraetFrei, sicherheitsnummer } from '@/services/e2eeGeraet'
import {
  uebergebeVerlauf,
  type KopplungsStatus,
  type UebergabeZiel,
} from '@/services/verlaufsUebergabe'
import { toast } from '@/stores/toastStore'

interface Geraet {
  family: string
  label: string
  paired_at: string | null
  is_active?: boolean
  last_active_at?: string | null
}

/** Die offene Rückfrage: welches Gerät, welche Nummer, an welchem Code abgelegt wird. */
interface Rueckfrage {
  code: string
  ziel: UebergabeZiel
  nummer: string
  /** Der Name aus der Einladung — falls das Gerät sich selbst keinen gegeben hat. */
  einladungsName: string
  /** Die Anmeldung aus dem Einlösen, zum Widerrufen. */
  family: string | null
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
  const [rueckfrage, setRueckfrage] = useState<Rueckfrage | null>(null)
  const [uebergibt, setUebergibt] = useState(false)

  const laden = () => {
    api<Geraet[]>('/auth/devices')
      .then(setGeraete)
      .catch(() => setGeraete([]))
  }

  useEffect(laden, [])

  /**
   * Wartet auf das Einlösen — und fragt dann, ob der Verlauf hinüber soll.
   *
   * Das neue Gerät veröffentlicht seinen Schlüssel erst nach dem Einlösen,
   * deshalb kann erst jetzt gegen ihn versiegelt werden. Steht nach ein paar
   * Runden noch kein Gerät in der Antwort, gibt diese Seite auf: eine Kopplung
   * ohne Verlaufsumzug ist unschön, eine hängende Karte wäre schlimmer.
   *
   * **Übergeben wird nur auf Bestätigung.** Welche Geräte „neu" sind, sagt der
   * Server, und genau dort könnte er ein eigenes unterschieben. Bis 09/2026
   * gingen Verlauf und Notizschlüssel ohne Nachfrage an jedes Gerät, das er
   * nannte. Jetzt stehen Name und Sicherheitsnummer auf der Karte, die App
   * zeigt dieselbe Nummer, und erst der Klick übergibt. Hat sich mehr als ein
   * Gerät gemeldet, wird gar nicht übergeben: welches das richtige ist, ließe
   * sich nur raten.
   */
  useEffect(() => {
    if (!code) return
    let aktiv = true
    let versuche = 0
    const schliessen = () => {
      setCode(null)
      setQrDataUri(null)
      laden()
    }
    const interval = setInterval(async () => {
      try {
        const res = await api<KopplungsStatus>(
          `/auth/devices/pairing/${encodeURIComponent(code)}/status`,
        )
        if (!aktiv) return
        if (res.redeemed) {
          const ziele = res.neue_geraete ?? []
          versuche += 1
          if (ziele.length === 0 && versuche < 8 && !res.verlauf_abgelegt) {
            // Das Gerät meldet seinen Schlüssel gleich. Noch eine Runde warten.
            return
          }
          aktiv = false
          clearInterval(interval)
          if (ziele.length > 1 && !res.verlauf_abgelegt) {
            toast.error(t('ai.profile.devicePairTooMany'))
            schliessen()
            return
          }
          if (ziele.length === 1 && !res.verlauf_abgelegt) {
            const ziel = ziele[0]
            const nummer = await sicherheitsnummer(ziel.public_key)
            setRueckfrage({
              code,
              ziel,
              nummer,
              einladungsName: res.label ?? '',
              family: res.family ?? null,
            })
            schliessen()
            return
          }
          // Schon übergeben — etwa aus einem zweiten Tab — oder kein Gerät
          // gemeldet: die Kopplung steht, eine Rückfrage gibt es nicht.
          toast.success(t('ai.profile.devicePairSuccess'))
          schliessen()
        } else if (res.expired) {
          toast.error(t('ai.profile.devicePairExpired'))
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

  /**
   * Der Eintrag des gekoppelten Geräts, wie der Server ihn hält — mit
   * Signaturschlüssel, über den die Freigabe unterschreibt. Nur wenn der
   * Verschlüsselungsschlüssel noch derselbe ist, dessen Nummer verglichen wurde.
   */
  const verzeichnisEintrag = async (ziel: UebergabeZiel) => {
    const konto = angemeldetesKonto()
    if (!konto) return null
    const liste = await getE2eeGeraete(konto, true)
    return liste.find((g) => g.device_id === ziel.device_id && g.public_key === ziel.public_key) ?? null
  }

  const uebergeben = async () => {
    if (!rueckfrage) return
    setUebergibt(true)
    try {
      // Die Nummern stimmen: das ist die Freigabe. Ohne sie bekäme das neue
      // Gerät keine Nachrichten, sondern nur den Verlauf.
      const eintrag = await verzeichnisEintrag(rueckfrage.ziel)
      if (eintrag && eintrag.is_approved === false) await gebeGeraetFrei(eintrag)
      const abgelegt = await uebergebeVerlauf(rueckfrage.code, [rueckfrage.ziel])
      toast.success(
        t(abgelegt ? 'ai.profile.devicePairHandedOver' : 'ai.profile.devicePairSuccess'),
      )
    } catch {
      // Der Verlauf bleibt beim alten Gerät. Die Kopplung selbst steht.
      toast.error(t('ai.profile.devicePairHandOverFailed'))
    } finally {
      setUebergibt(false)
      setRueckfrage(null)
    }
  }

  const ablehnen = () => {
    setRueckfrage(null)
    toast.info(t('ai.profile.devicePairDeclined'))
  }

  /**
   * Das Gerät wieder hinauswerfen — für den Fall, dass die Nummern nicht passen.
   *
   * Nur die Karte zu schliessen liess es im Verzeichnis: der nächste Abgleich
   * schickte ihm den Notizschlüssel, und jede neue Nachricht ging auch an
   * seinen Schlüssel. Deshalb zwei Schritte, in dieser Reihenfolge: erst die
   * Anmeldung widerrufen — ohne sie trägt es sich nicht wieder ein —, dann die
   * Zustelladresse entfernen. Ist die Anmeldung schon weg (404, etwa aus einem
   * zweiten Tab), fehlt nur noch die Adresse.
   */
  const entfernen = async () => {
    if (!rueckfrage) return
    setUebergibt(true)
    try {
      if (rueckfrage.family) {
        try {
          await api(`/auth/devices/${encodeURIComponent(rueckfrage.family)}`, { method: 'DELETE' })
        } catch (err: any) {
          if (err?.status !== 404) throw err
        }
      }
      // Vergisst auch den Cache: sonst verschlüsselte dieser Tab noch bis zu
      // zehn Minuten lang auch an das gerade entfernte Gerät.
      const eintrag = await verzeichnisEintrag(rueckfrage.ziel)
      if (eintrag) await entferneGeraet(eintrag)
      toast.success(t('ai.profile.devicePairRemoved'))
      setRueckfrage(null)
    } catch (err: any) {
      // Die Rückfrage bleibt stehen: entfernt ist womöglich noch nichts, und
      // der Knopf soll ein zweites Mal gehen.
      toast.error(err?.message || t('common.error'))
    } finally {
      setUebergibt(false)
      laden()
    }
  }

  const entziehen = async (geraet: Geraet) => {
    try {
      await api(`/auth/devices/${encodeURIComponent(geraet.family)}`, { method: 'DELETE' })
      toast.success(t('ai.profile.deviceRevoked'))
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
        <h2 id="ai-devices-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('ai.profile.devicesTitle')}
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
          {t('ai.profile.devicesApiAddress')}
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
            {t('common.copy')}
          </Button>
        </div>
        <p className="msm-field-help">{t('ai.profile.devicesApiAddressHint')}</p>
      </div>

      {rueckfrage ? (
        <div
          className="max-w-xl space-y-3 rounded-lg border border-outline-variant/40 bg-surface-container-high/40 p-4"
          role="group"
          aria-labelledby="kopplung-rueckfrage-titel"
        >
          <h3 id="kopplung-rueckfrage-titel" className="text-sm font-semibold text-on-surface">
            {t('ai.profile.devicePairConfirmTitle')}
          </h3>
          <p className="text-sm text-on-surface-variant">{t('ai.profile.devicePairConfirmHint')}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-on-surface-variant">{t('ai.profile.devicePairDevice')}</dt>
            <dd className="min-w-0 truncate text-on-surface">
              {rueckfrage.ziel.label || rueckfrage.einladungsName || t('ai.profile.devicesUnnamed')}
            </dd>
            {rueckfrage.ziel.created_at && (
              <>
                <dt className="text-on-surface-variant">{t('ai.profile.devicePairRegistered')}</dt>
                <dd className="text-on-surface">
                  {new Date(rueckfrage.ziel.created_at).toLocaleString()}
                </dd>
              </>
            )}
            <dt className="text-on-surface-variant">{t('ai.profile.devicePairSafetyNumber')}</dt>
            <dd className="font-mono tracking-wider text-on-surface">{rueckfrage.nummer}</dd>
          </dl>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => void entfernen()}
              disabled={uebergibt}
              className="text-error hover:bg-error/10 hover:text-error"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('ai.profile.devicePairRemove')}
            </Button>
            <Button variant="secondary" onClick={ablehnen} disabled={uebergibt}>
              {t('ai.profile.devicePairDecline')}
            </Button>
            <Button onClick={() => void uebergeben()} disabled={uebergibt}>
              {t('ai.profile.devicePairHandOver')}
            </Button>
          </div>
        </div>
      ) : code ? (
        <SecretOnce
          label={t('ai.profile.devicesCodeLabel')}
          value={code}
          qrDataUri={qrDataUri}
          hinweis={t('ai.profile.devicesOnceHint')}
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
              {t('ai.profile.devicesNameLabel')}
            </span>
            <input
              className="msm-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('ai.profile.devicesNamePlaceholder')}
              maxLength={64}
            />
          </label>
          <Button onClick={koppeln} disabled={busy}>
            {t('ai.profile.devicesPair')}
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
                    {geraet.label || t('ai.profile.devicesUnnamed')}
                  </span>
                  {geraet.is_active !== false ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-status-success/10 px-2 py-0.5 text-xs font-medium text-status-success border border-status-success/20">
                      <span className="h-1.5 w-1.5 rounded-full bg-status-success animate-pulse" />
                      {t('ai.profile.deviceActive')}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-2 py-0.5 text-xs font-medium text-on-surface-variant border border-outline-variant/30">
                      <span className="h-1.5 w-1.5 rounded-full bg-on-surface-variant/50" />
                      {t('ai.profile.deviceInactive')}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-on-surface-variant">
                  {geraet.last_active_at && (
                    <span>
                      {t('ai.profile.deviceLastActive')}:{' '}
                      {formatRelativeTime(geraet.last_active_at, t)}
                    </span>
                  )}
                  {geraet.paired_at && (
                    <span>
                      {t('ai.profile.devicePairedAt')}:{' '}
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
                {t('ai.profile.devicesRevoke')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
