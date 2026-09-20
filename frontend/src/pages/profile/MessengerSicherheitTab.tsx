/**
 * Messenger-PIN und automatische Sperre.
 *
 * Dieselbe Komponente in beiden Bauten: die Web-Oberfläche zeigt sie unter
 * Profil, die App unter Einstellungen. Zwei Abschriften wären zwei Stände.
 *
 * Was die Oberfläche hier leisten muss, steht nicht im Code, sondern in dem,
 * was sie sagt: Ein PIN, der den Verlauf verschlüsselt, ist etwas anderes als
 * ein Vorhang davor, und ein Gerätegeheimnis, das verloren gehen kann, ist eine
 * Zusage mit Preisschild. Beides muss dastehen, bevor jemand den Schalter
 * umlegt — nicht in einer Fußnote danach.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Fingerprint, Lock, ShieldCheck, Timer } from 'lucide-react'

import { PasswordInput } from '@/components/ui/PasswordInput'
import { Button, Dropdown, type DropdownOption, Switch } from '@/Singra/UI'
import { sperrfristOptionen } from '@/services/autoSperre'
import { PIN_MINDESTLAENGE, useMessengerSperre } from '@/services/messengerSperre'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

type Formular = 'keines' | 'einrichten' | 'aendern' | 'abschalten' | 'biometrie'

function fehlertext(fehler: unknown, ersatz: string): string {
  return fehler instanceof Error && fehler.message ? fehler.message : ersatz
}

export function MessengerSicherheitTab() {
  const { t } = useTranslation()

  const eingerichtet = useMessengerSperre((s) => s.eingerichtet)
  const laeuft = useMessengerSperre((s) => s.laeuft)
  const biometrieMoeglich = useMessengerSperre((s) => s.biometrieMoeglich)
  const biometrieAktiv = useMessengerSperre((s) => s.biometrieAktiv)
  const geraetebindung = useMessengerSperre((s) => s.geraetebindung)
  const sperrfrist = useMessengerSperre((s) => s.sperrfrist)
  const sperrtBeiFensterwechsel = useMessengerSperre((s) => s.sperrtBeiFensterwechsel)

  const [formular, setFormular] = useState<Formular>('keines')
  const [pin, setPin] = useState('')
  const [pinWiederholung, setPinWiederholung] = useState('')
  const [neuerPin, setNeuerPin] = useState('')

  useEffect(() => {
    void useMessengerSperre.getState().initialisiere()
  }, [])

  const formularSchliessen = () => {
    setFormular('keines')
    setPin('')
    setPinWiederholung('')
    setNeuerPin('')
  }

  const zuKurz = pin.length > 0 && pin.length < PIN_MINDESTLAENGE
  const passtNicht = pinWiederholung.length > 0 && pin !== pinWiederholung

  const einrichten = async () => {
    if (pin.length < PIN_MINDESTLAENGE || pin !== pinWiederholung) return
    try {
      await useMessengerSperre.getState().einrichten(pin)
      formularSchliessen()
      toast.success(
        t('profile.messengerLock.toast.enabled', 'Der Messenger ist jetzt mit einem PIN gesichert.'),
      )
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.enableFailed', 'Der PIN liess sich nicht einrichten.'),
        ),
      )
    }
  }

  const aendern = async () => {
    if (neuerPin.length < PIN_MINDESTLAENGE || neuerPin !== pinWiederholung) return
    try {
      await useMessengerSperre.getState().pinAendern(pin, neuerPin)
      formularSchliessen()
      toast.success(t('profile.messengerLock.toast.changed', 'Der PIN wurde geändert.'))
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.changeFailed', 'Der PIN liess sich nicht ändern.'),
        ),
      )
    }
  }

  const abschalten = async () => {
    if (!pin) return
    const sicher = await confirm({
      title: t('profile.messengerLock.confirmOff.title', 'Sperre aufheben?'),
      message: t(
        'profile.messengerLock.confirmOff.message',
        'Danach liegen Verlauf, Geräteausweis und Schlüssel wieder unverschlüsselt auf diesem Gerät. Wer Zugriff auf den Rechner hat, kann sie lesen.',
      ),
      confirmText: t('profile.messengerLock.confirmOff.ok', 'Sperre aufheben'),
      danger: true,
    })
    if (!sicher) return
    try {
      await useMessengerSperre.getState().abschalten(pin)
      formularSchliessen()
      toast.success(t('profile.messengerLock.toast.disabled', 'Die Sperre ist aufgehoben.'))
    } catch (fehler) {
      toast.error(
        fehlertext(fehler, t('profile.messengerLock.toast.wrongPin', 'Der PIN stimmt nicht.')),
      )
    }
  }

  const biometrieUmschalten = async (an: boolean) => {
    if (!an) {
      await useMessengerSperre.getState().biometrieAusschalten()
      toast.success(
        t('profile.messengerLock.toast.bioOff', 'Der Schnelleinstieg ist abgeschaltet.'),
      )
      return
    }
    setFormular('biometrie')
  }

  const biometrieEinrichten = async () => {
    if (!pin) return
    try {
      await useMessengerSperre.getState().biometrieEinschalten(pin)
      formularSchliessen()
      toast.success(
        t('profile.messengerLock.toast.bioOn', 'Der Messenger lässt sich jetzt per Finger öffnen.'),
      )
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.bioFailed', 'Der Schnelleinstieg liess sich nicht einrichten.'),
        ),
      )
    }
  }

  const fristOptionen: DropdownOption[] = sperrfristOptionen(t)

  return (
    <div className="space-y-4">
      {/* PIN */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Lock className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">
              {t('profile.messengerLock.title', 'Messenger sperren')}
            </h2>
            <p className="text-label-sm text-on-surface-variant">
              {eingerichtet
                ? t('profile.messengerLock.stateOn', 'Auf diesem Gerät eingerichtet.')
                : t('profile.messengerLock.stateOff', 'Auf diesem Gerät nicht eingerichtet.')}
            </p>
          </div>
        </div>

        <div className="space-y-3 pt-2 border-t border-outline-variant/30">
          <p className="text-xs text-on-surface-variant leading-relaxed">
            {t(
              'profile.messengerLock.explain',
              'Damit dein Verlauf ein Neuladen übersteht, liegt er auf diesem Gerät. Ohne PIN liegt er dort lesbar: wer an den Rechner kommt, kommt an die Nachrichten und an den Geräteausweis, mit dem sich dieses Gerät ausweist. Mit PIN wird beides verschlüsselt.',
            )}
          </p>

          {eingerichtet && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-label-sm text-on-surface-variant">
              {geraetebindung
                ? t(
                    'profile.messengerLock.boundNote',
                    'Der Schlüssel hängt zusätzlich am Schlüsselspeicher dieses Rechners. Eine kopierte Festplatte ist anderswo damit wertlos.',
                  )
                : t(
                    'profile.messengerLock.unboundNote',
                    'Auf dieser Plattform gibt es keinen geschützten Schlüsselspeicher. Der Schutz hängt allein an der Länge deines PIN — je länger, desto besser.',
                  )}
            </div>
          )}

          {!eingerichtet && formular !== 'einrichten' && (
            <Button onClick={() => setFormular('einrichten')} disabled={laeuft}>
              {t('profile.messengerLock.setUp', 'PIN einrichten')}
            </Button>
          )}

          {formular === 'einrichten' && (
            <div className="space-y-3">
              <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-label-sm text-on-surface-variant leading-relaxed">
                {t(
                  'profile.messengerLock.warnLoss',
                  'Merk dir diesen PIN. Es gibt keinen Ersatzweg und keinen Wiederherstellungsschlüssel. Deine eigenen gesendeten Nachrichten stehen nirgendwo sonst, auch nicht auf dem Server — wer den PIN vergisst oder diesen Rechner neu aufsetzt, verliert den Verlauf dieses Geräts.',
                )}
              </div>
              <PasswordInput
                label={t('profile.messengerLock.newPin', 'Neuer PIN')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="new-password"
                error={
                  zuKurz
                    ? t('profile.messengerLock.tooShort', 'Mindestens {{count}} Zeichen.', {
                        count: PIN_MINDESTLAENGE,
                      })
                    : undefined
                }
              />
              <PasswordInput
                label={t('profile.messengerLock.repeat', 'PIN wiederholen')}
                value={pinWiederholung}
                onChange={(e) => setPinWiederholung(e.target.value)}
                autoComplete="new-password"
                error={
                  passtNicht
                    ? t('profile.messengerLock.mismatch', 'Die beiden Eingaben stimmen nicht überein.')
                    : undefined
                }
              />
              <div className="flex gap-2">
                <Button
                  onClick={einrichten}
                  disabled={laeuft || pin.length < PIN_MINDESTLAENGE || pin !== pinWiederholung}
                >
                  {laeuft
                    ? t('profile.messengerLock.working', 'Einen Moment …')
                    : t('profile.messengerLock.setUp', 'PIN einrichten')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel', 'Abbrechen')}
                </Button>
              </div>
            </div>
          )}

          {eingerichtet && formular === 'keines' && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setFormular('aendern')}>
                {t('profile.messengerLock.change', 'PIN ändern')}
              </Button>
              <Button variant="ghost" onClick={() => setFormular('abschalten')}>
                {t('profile.messengerLock.turnOff', 'Sperre aufheben')}
              </Button>
            </div>
          )}

          {formular === 'aendern' && (
            <div className="space-y-3">
              <PasswordInput
                label={t('profile.messengerLock.currentPin', 'Bisheriger PIN')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="current-password"
              />
              <PasswordInput
                label={t('profile.messengerLock.newPin', 'Neuer PIN')}
                value={neuerPin}
                onChange={(e) => setNeuerPin(e.target.value)}
                autoComplete="new-password"
              />
              <PasswordInput
                label={t('profile.messengerLock.repeat', 'PIN wiederholen')}
                value={pinWiederholung}
                onChange={(e) => setPinWiederholung(e.target.value)}
                autoComplete="new-password"
              />
              <div className="flex gap-2">
                <Button
                  onClick={aendern}
                  disabled={
                    laeuft || !pin || neuerPin.length < PIN_MINDESTLAENGE || neuerPin !== pinWiederholung
                  }
                >
                  {t('profile.messengerLock.change', 'PIN ändern')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel', 'Abbrechen')}
                </Button>
              </div>
            </div>
          )}

          {formular === 'abschalten' && (
            <div className="space-y-3">
              <PasswordInput
                label={t('profile.messengerLock.currentPin', 'Bisheriger PIN')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="current-password"
              />
              <div className="flex gap-2">
                <Button variant="destructive" onClick={abschalten} disabled={laeuft || !pin}>
                  {t('profile.messengerLock.turnOff', 'Sperre aufheben')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel', 'Abbrechen')}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Biometrie */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Fingerprint className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">
              {t('profile.messengerLock.bioTitle', 'Mit Fingerabdruck öffnen')}
            </h2>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          {!biometrieMoeglich ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant leading-relaxed">
              {t(
                'profile.messengerLock.bioUnavailable',
                'Hier nicht möglich. Der Schnelleinstieg braucht einen Schlüsselspeicher in der Hardware: den haben die Desktop-App (Windows Hello) und die Android-App (Fingerabdruck), der Browser nicht. Der PIN selbst funktioniert überall.',
              )}
            </div>
          ) : !eingerichtet ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t('profile.messengerLock.bioNeedsPin', 'Richte zuerst einen PIN ein.')}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <span className="text-xs font-medium text-on-surface">
                    {t('profile.messengerLock.bioSwitch', 'Ohne Tippen entsperren')}
                  </span>
                  <p className="text-label-sm text-on-surface-variant">
                    {t(
                      'profile.messengerLock.bioHint',
                      'Der PIN wird im Schlüsselspeicher des Systems hinterlegt und erst nach erfolgreicher Bestätigung herausgegeben.',
                    )}
                  </p>
                </div>
                <Switch
                  checked={biometrieAktiv}
                  disabled={laeuft}
                  onCheckedChange={(an) => void biometrieUmschalten(an)}
                />
              </div>

              {formular === 'biometrie' && (
                <div className="space-y-3 pt-2 border-t border-outline-variant/20">
                  <PasswordInput
                    label={t('profile.messengerLock.currentPin', 'Bisheriger PIN')}
                    value={pin}
                    onChange={(e) => setPin(e.target.value)}
                    autoComplete="current-password"
                  />
                  <div className="flex gap-2">
                    <Button onClick={biometrieEinrichten} disabled={laeuft || !pin}>
                      {t('profile.messengerLock.bioSetUp', 'Hinterlegen')}
                    </Button>
                    <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                      {t('common.cancel', 'Abbrechen')}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Automatische Sperre */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Timer className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">
              {t('profile.messengerLock.autoTitle', 'Automatisch sperren')}
            </h2>
            <p className="text-label-sm text-on-surface-variant">
              {t(
                'profile.messengerLock.autoSubtitle',
                'Gilt nur für den Messenger. Der Tresor hat eine eigene Frist.',
              )}
            </p>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          {!eingerichtet && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t(
                'profile.messengerLock.autoNeedsPin',
                'Ohne PIN gibt es nichts zu sperren. Die Einstellung greift, sobald einer eingerichtet ist.',
              )}
            </div>
          )}

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <label className="text-xs font-medium text-on-surface">
              {t('profile.messengerLock.afterIdle', 'Bei Untätigkeit')}
            </label>
            <div className="w-full sm:w-56">
              <Dropdown
                options={fristOptionen}
                value={String(sperrfrist)}
                onChange={(wert) => useMessengerSperre.getState().setzeSperrfrist(Number(wert))}
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">
                {t('profile.messengerLock.onBlur', 'Beim Fensterwechsel sperren')}
              </span>
              <p className="text-label-sm text-on-surface-variant">
                {t(
                  'profile.messengerLock.onBlurHint',
                  'Sperrt, sobald das Fenster in den Hintergrund geht oder der Reiter gewechselt wird.',
                )}
              </p>
            </div>
            <Switch
              checked={sperrtBeiFensterwechsel}
              onCheckedChange={(an) => useMessengerSperre.getState().setzeFensterwechsel(an)}
            />
          </div>
        </div>
      </div>

      <div className="flex items-start gap-2 px-1 text-label-sm text-on-surface-variant">
        <ShieldCheck className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        <p className="leading-relaxed">
          {t(
            'profile.messengerLock.footnote',
            'Die Verschlüsselung zwischen dir und deinem Gegenüber bleibt davon unberührt. Der Server konnte deine Nachrichten nie lesen und kann es weiterhin nicht. Der PIN schützt, was auf diesem Gerät liegt.',
          )}
        </p>
      </div>
    </div>
  )
}
