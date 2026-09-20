import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Loader2, PhoneCall, PlugZap, Save, XCircle } from 'lucide-react'
import { Button, Dropdown, Input } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'
import {
  holeLivekitStatus,
  speichereLivekitKonfiguration,
  testeLivekitVerbindung,
  type LivekitStatus,
} from '@/api/calls'

type Modus = 'lokal' | 'extern'

/**
 * Wo die Anrufe des Messengers herkommen.
 *
 * Standard ist der mitgelieferte Medienserver, der bei der Installation
 * eingerichtet wird. Wer LiveKit Cloud oder einen eigenen Server betreibt,
 * trägt ihn hier ein.
 */
export function MessengerTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')

  const [status, setStatus] = useState<LivekitStatus | null>(null)
  const [laedt, setLaedt] = useState(true)
  const [modus, setModus] = useState<Modus>('lokal')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [speichert, setSpeichert] = useState(false)
  const [testet, setTestet] = useState(false)
  const [testErgebnis, setTestErgebnis] = useState<{ ok: boolean; text: string } | null>(null)

  /**
   * Die Eingabefelder werden genau einmal befüllt: beim ersten Laden.
   *
   * Sonst könnte eine spät eintreffende Statusantwort überschreiben, was der
   * Betreiber inzwischen ausgewählt oder eingetippt hat — die Auswahl spränge
   * vor seinen Augen zurück.
   */
  const bereitsBefuellt = useRef(false)

  const uebernehme = (neu: LivekitStatus) => {
    setStatus(neu)
    // Schlüssel und Geheimnis bleiben leer. Was gespeichert ist, steht maskiert
    // im Platzhalter; leer abgeschickt heißt „unverändert lassen".
    setApiKey('')
    setApiSecret('')
    if (!bereitsBefuellt.current) {
      bereitsBefuellt.current = true
      setModus(neu.modus)
      setUrl(neu.url)
    }
  }

  useEffect(() => {
    let aktiv = true
    holeLivekitStatus()
      .then((daten) => {
        if (aktiv) uebernehme(daten)
      })
      .catch(() => {
        if (aktiv) {
          toast.error(
            t('settings.messenger.errors.load', 'Der Anruf-Status konnte nicht geladen werden.'),
          )
        }
      })
      .finally(() => {
        if (aktiv) setLaedt(false)
      })
    return () => {
      aktiv = false
    }
    // Absichtlich nur beim Aufbau: ein Sprachwechsel soll die Felder nicht neu laden.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Ist für diesen Modus schon etwas gespeichert, das leere Felder meinen können? */
  const hatBestand = status?.modus === 'extern' && status.konfiguriert

  const speichern = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || speichert) return
    setSpeichert(true)
    setTestErgebnis(null)
    try {
      const neu = await speichereLivekitKonfiguration({
        modus,
        url: modus === 'extern' ? url.trim() : undefined,
        api_key: modus === 'extern' ? apiKey.trim() || undefined : undefined,
        api_secret: modus === 'extern' ? apiSecret.trim() || undefined : undefined,
      })
      uebernehme(neu)
      toast.success(t('settings.messenger.saved', 'Gespeichert.'))
    } catch (fehler: unknown) {
      toast.error(fehler instanceof Error ? fehler.message : String(fehler))
    } finally {
      setSpeichert(false)
    }
  }

  const testen = async () => {
    if (testet) return
    setTestet(true)
    setTestErgebnis(null)
    try {
      const ergebnis = await testeLivekitVerbindung({
        url: url.trim(),
        api_key: apiKey.trim() || undefined,
        api_secret: apiSecret.trim() || undefined,
      })
      setTestErgebnis({ ok: ergebnis.erreichbar, text: ergebnis.meldung })
    } catch (fehler: unknown) {
      setTestErgebnis({
        ok: false,
        text: fehler instanceof Error ? fehler.message : String(fehler),
      })
    } finally {
      setTestet(false)
    }
  }

  if (laedt) {
    return (
      <section className="msm-card flex items-center justify-center p-10">
        <Loader2 className="h-5 w-5 animate-spin text-on-surface-variant" />
      </section>
    )
  }

  return (
    <section className="msm-card space-y-5 p-6" aria-labelledby="messenger-calls-title">
      <div className="flex items-center gap-2">
        <PhoneCall className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3 id="messenger-calls-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('settings.messenger.title', 'Anrufe')}
        </h3>
      </div>

      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t(
          'settings.messenger.description',
          'Sprach-, Video- und Gruppenanrufe laufen über einen Medienserver. Der mitgelieferte läuft auf diesem Host und ist bereits eingerichtet. Ton und Bild bleiben in beiden Fällen Ende-zu-Ende verschlüsselt: der Medienserver leitet weiter, was er nicht öffnen kann.',
        )}
      </p>

      {/* Statusstreifen */}
      <div
        className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-2.5 text-sm ${
          status?.erreichbar
            ? 'border-status-success/30 bg-status-success/10 text-status-success'
            : 'border-status-warning/30 bg-status-warning/10 text-status-warning'
        }`}
        role="status"
      >
        {status?.erreichbar ? (
          <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
        ) : (
          <XCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
        )}
        <span className="font-medium">
          {status?.erreichbar
            ? t('settings.messenger.reachable', 'Anrufserver erreichbar')
            : t('settings.messenger.unreachable', 'Anrufserver nicht erreichbar')}
        </span>
        {status?.url && <span className="font-mono text-xs opacity-80">{status.url}</span>}
        {status?.erreichbar && (
          <span className="text-xs opacity-80">
            {t('settings.messenger.activeRooms', '{{count}} aktive Räume', {
              count: status.raeume_aktiv,
            })}
          </span>
        )}
        {status?.fehler && <span className="w-full text-xs opacity-90">{status.fehler}</span>}
      </div>

      <form className="space-y-5" onSubmit={speichern}>
        <div className="max-w-md space-y-1.5">
          <span
            id="livekit-modus-label"
            className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant"
          >
            {t('settings.messenger.mode', 'Medienserver')}
          </span>
          <Dropdown
            value={modus}
            onChange={(wert) => setModus(wert as Modus)}
            disabled={!canWrite}
            aria-label={t('settings.messenger.mode', 'Medienserver')}
            data-testid="livekit-modus"
            options={[
              {
                value: 'lokal',
                label: t('settings.messenger.modeLocal', 'Integriert (empfohlen)'),
                hint: t(
                  'settings.messenger.modeLocalHint',
                  'Läuft auf diesem Host, keine Konfiguration nötig',
                ),
              },
              {
                value: 'extern',
                label: t('settings.messenger.modeExternal', 'Externer Server'),
                hint: t(
                  'settings.messenger.modeExternalHint',
                  'LiveKit Cloud oder ein eigener LiveKit-Server',
                ),
              },
            ]}
          />
        </div>

        {modus === 'extern' && (
          <div className="max-w-2xl space-y-3">
            <label className="block space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.messenger.url', 'Server-URL')}
              </span>
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={!canWrite}
                placeholder="wss://mein-projekt.livekit.cloud"
                aria-label={t('settings.messenger.url', 'Server-URL')}
              />
            </label>

            <label className="block space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.messenger.apiKey', 'API-Key')}
              </span>
              <Input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                disabled={!canWrite}
                placeholder={
                  hatBestand && status?.api_key_maskiert
                    ? status.api_key_maskiert
                    : 'APIxxxxxxxxxxxx'
                }
                aria-label={t('settings.messenger.apiKey', 'API-Key')}
              />
            </label>

            <label className="block space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.messenger.apiSecret', 'API-Secret')}
              </span>
              <PasswordInput
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                disabled={!canWrite}
                autoComplete="new-password"
                placeholder={
                  hatBestand
                    ? t(
                        'settings.messenger.secretStored',
                        'Gespeichert. Leer lassen, um es zu behalten.',
                      )
                    : ''
                }
                aria-label={t('settings.messenger.apiSecret', 'API-Secret')}
              />
            </label>

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Button
                type="button"
                variant="secondary"
                onClick={() => void testen()}
                disabled={testet || !url.trim() || (!apiKey.trim() && !hatBestand)}
              >
                {testet ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <PlugZap className="h-4 w-4" aria-hidden="true" />
                )}
                {t('settings.messenger.test', 'Verbindung testen')}
              </Button>
              {testErgebnis && (
                <span
                  className={`text-sm ${testErgebnis.ok ? 'text-status-success' : 'text-status-destructive'}`}
                  role="status"
                >
                  {testErgebnis.text}
                </span>
              )}
            </div>
          </div>
        )}

        {canWrite && (
          <div className="pt-1">
            <Button type="submit" disabled={speichert}>
              {speichert ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Save className="h-4 w-4" aria-hidden="true" />
              )}
              {t('settings.save', 'Speichern')}
            </Button>
          </div>
        )}
      </form>

      <p className="text-xs leading-relaxed text-on-surface-variant">
        {t(
          'settings.messenger.metadataHint',
          'Der Medienserver sieht keine Gesprächsinhalte, aber er weiß, welche Kennungen wann in welchem Raum waren. Bei einem externen Anbieter liegt diese Information dort.',
        )}
      </p>
    </section>
  )
}
