import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Clock, ShieldCheck, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { getE2eeGeraete, type E2eeGeraetItem } from '@/api/social'
import { Button } from '@/Singra/UI'
import {
  eigenesGeraet,
  entferneGeraet,
  gebeGeraetFrei,
  geraeteZuruecksetzen,
  sicherheitsnummer,
} from '@/services/e2eeGeraet'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/**
 * Die Geräte, an die Nachrichten für dieses Konto verschlüsselt werden.
 *
 * Bis 09/2026 gab es diese Liste nicht, und das war eine Lücke: wer sich in
 * einem fremden Browser angemeldet hatte, ließ dort einen Schlüssel zurück, der
 * weiter Post bekam. Die blinde Mailbox steht bewusst jedem Angemeldeten offen,
 * der Geräteschlüssel ist also die einzige Schranke davor. Sichtbar war er
 * nirgends, und entfernen ließ er sich schon gar nicht.
 *
 * Seit 09/2026 sperrt Entfernen aus: die Sitzung des Geräts fällt sofort, und
 * meldet es sich wieder an, wartet es auf Freigabe. Freigeben und Entfernen
 * unterschreibt dieses Gerät (`gebeGeraetFrei`, `entferneGeraet`) — und kann
 * es deshalb nur, solange es selbst freigegeben ist.
 *
 * Das eigene Gerät steht in der Liste, hat aber keinen Knopf. Es würde sich
 * sofort wieder eintragen, und im selben Tab nicht einmal das:
 * `geraetVeroeffentlichen` meldet sich je Sitzung genau einmal.
 */
export function E2eeGeraeteCard() {
  const { t } = useTranslation()
  const eigeneId = useAuthStore((s) => s.user?.id)
  const [geraete, setGeraete] = useState<E2eeGeraetItem[] | null>(null)
  const [sicherheitsnummern, setSicherheitsnummern] = useState<Record<string, string>>({})
  const [fehler, setFehler] = useState(false)
  const [meineKennung, setMeineKennung] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState<string | null>(null)
  const [passwort, setPasswort] = useState('')

  const laden = useCallback(async () => {
    if (!eigeneId) return
    try {
      // Direkt und nicht über `geraeteVon`: dessen Zehn-Minuten-Frist trägt den
      // Sendepfad, hier zeigte sie einen Stand von vor zehn Minuten an.
      // includeUnapproved = true, damit ausstehende Geräte freigegeben werden können.
      const liste = await getE2eeGeraete(eigeneId, true)
      setGeraete(liste)
      setFehler(false)
      const nummern: Record<string, string> = {}
      for (const item of liste) {
        if (item.public_key) {
          try {
            nummern[item.device_id] = await sicherheitsnummer(item.public_key)
          } catch {
            nummern[item.device_id] = ''
          }
        }
      }
      setSicherheitsnummern(nummern)
    } catch {
      setFehler(true)
    }
  }, [eigeneId])

  useEffect(() => {
    void laden()
  }, [laden])

  useEffect(() => {
    eigenesGeraet()
      .then((g) => setMeineKennung(g.kennung))
      .catch(() => setMeineKennung(null))
  }, [eigeneId])

  const freigeben = async (geraet: E2eeGeraetItem) => {
    setLaeuft(geraet.device_id)
    try {
      await gebeGeraetFrei(geraet)
      toast.success(t('profile.e2eeDevices.approved'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const entfernen = async (geraet: E2eeGeraetItem) => {
    const ok = await confirm({
      title: t('profile.e2eeDevices.removeTitle'),
      message: t('profile.e2eeDevices.removeMessage'),
      confirmText: t('profile.e2eeDevices.remove'),
      danger: true,
    })
    if (!ok) return

    setLaeuft(geraet.device_id)
    try {
      // Vergisst auch den Cache: sonst verschlüsselte dieser Tab noch bis zu
      // zehn Minuten lang gegen die gerade entfernte Adresse.
      await entferneGeraet(geraet)
      toast.success(t('profile.e2eeDevices.removed'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const neuBeginnen = async () => {
    if (!passwort) return
    setLaeuft('reset')
    try {
      await geraeteZuruecksetzen(passwort)
      setPasswort('')
      toast.success(t('profile.e2eeDevices.resetDone'))
      await laden()
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setLaeuft(null)
    }
  }

  const meinEintrag = geraete?.find((g) => g.device_id === meineKennung)
  // Freigeben und Entfernen freigegebener Geräte unterschreibt dieses Gerät;
  // der Server nimmt das nur von einem freigegebenen an.
  const ichBinFrei = meinEintrag?.is_approved !== false

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="e2ee-devices-title">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="e2ee-devices-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('profile.e2eeDevices.title')}
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('profile.e2eeDevices.description')}
      </p>

      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('profile.e2eeDevices.approvalHelp')}
      </p>

      {meinEintrag && !ichBinFrei && (
        <div className="space-y-3 rounded-lg border border-status-warning/30 bg-status-warning/10 p-4">
          <p className="text-sm text-on-surface">{t('profile.e2eeDevices.thisDevicePending')}</p>
          <div className="space-y-2">
            <p className="text-sm font-medium text-on-surface">{t('profile.e2eeDevices.resetTitle')}</p>
            <p className="text-sm text-on-surface-variant">{t('profile.e2eeDevices.resetHelp')}</p>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                type="password"
                autoComplete="current-password"
                aria-label={t('profile.e2eeDevices.resetPassword')}
                placeholder={t('profile.e2eeDevices.resetPassword')}
                value={passwort}
                onChange={(e) => setPasswort(e.target.value)}
                className="msm-input min-w-0 flex-1"
              />
              <Button
                variant="secondary"
                disabled={!passwort || laeuft === 'reset'}
                onClick={() => void neuBeginnen()}
                className="text-error hover:bg-error/10 hover:text-error"
              >
                {t('profile.e2eeDevices.resetButton')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {fehler && (
        <p className="text-sm text-on-surface-variant">
          {t('profile.e2eeDevices.loadFailed')}
        </p>
      )}

      {!fehler && geraete?.length === 0 && (
        <p className="text-sm text-on-surface-variant">
          {t('profile.e2eeDevices.empty')}
        </p>
      )}

      {!fehler && geraete && geraete.length > 0 && (
        <ul className="divide-y divide-outline-variant/30 border-t border-outline-variant/30 pt-2">
          {geraete.map((geraet) => {
            const istMeins = geraet.device_id === meineKennung
            const nummer = sicherheitsnummern[geraet.device_id]
            return (
              <li
                key={geraet.device_id}
                className="flex flex-col justify-between gap-4 py-3 sm:flex-row sm:items-center"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-on-surface">
                      {geraet.label || t('profile.e2eeDevices.unnamed')}
                    </span>
                    {istMeins && (
                      <span className="inline-flex items-center rounded-full border border-secondary/20 bg-secondary/10 px-2 py-0.5 text-xs font-medium text-secondary">
                        {t('profile.e2eeDevices.thisDevice')}
                      </span>
                    )}
                    {geraet.is_approved === false && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-status-warning/30 bg-status-warning/10 px-2 py-0.5 text-xs font-medium text-status-warning">
                        <Clock className="w-3 h-3" />
                        {t('profile.e2eeDevices.pending')}
                      </span>
                    )}
                  </div>
                  {/* Die Kennung steht im Klartext in jedem Umschlag. Sie hier
                      zu zeigen verrät nichts und ist der einzige Weg, zwei
                      unbenannte Browser auseinanderzuhalten. */}
                  <p className="font-mono text-xs text-on-surface-variant">
                    {geraet.device_id.slice(0, 12)}
                    {nummer && (
                      <span className="block sm:inline sm:ml-2 text-on-surface-variant/80">
                        {t('profile.e2eeDevices.safetyNumber')}: {nummer}
                      </span>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2 self-start sm:self-auto">
                  {!istMeins && ichBinFrei && geraet.is_approved === false && (
                    <Button
                      variant="primary"
                      disabled={laeuft === geraet.device_id}
                      onClick={() => void freigeben(geraet)}
                    >
                      <CheckCircle2 className="h-4 w-4 mr-1" aria-hidden="true" />
                      {t('profile.e2eeDevices.approve')}
                    </Button>
                  )}
                  {!istMeins && (ichBinFrei || geraet.is_approved === false) && (
                    <Button
                      variant="secondary"
                      disabled={laeuft === geraet.device_id}
                      onClick={() => void entfernen(geraet)}
                      className="text-error hover:bg-error/10 hover:text-error"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                      {t('profile.e2eeDevices.remove')}
                    </Button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
