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
import { DisBadge } from '@/components/DisBadge'

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
        t('profile.messengerLock.toast.enabled'),
      )
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.enableFailed'),
        ),
      )
    }
  }

  const aendern = async () => {
    if (neuerPin.length < PIN_MINDESTLAENGE || neuerPin !== pinWiederholung) return
    try {
      await useMessengerSperre.getState().pinAendern(pin, neuerPin)
      formularSchliessen()
      toast.success(t('profile.messengerLock.toast.changed'))
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.changeFailed'),
        ),
      )
    }
  }

  const abschalten = async () => {
    if (!pin) return
    const sicher = await confirm({
      title: t('profile.messengerLock.confirmOff.title'),
      message: t('profile.messengerLock.confirmOff.message'),
      confirmText: t('profile.messengerLock.confirmOff.ok'),
      danger: true,
    })
    if (!sicher) return
    try {
      await useMessengerSperre.getState().abschalten(pin)
      formularSchliessen()
      toast.success(t('profile.messengerLock.toast.disabled'))
    } catch (fehler) {
      toast.error(
        fehlertext(fehler, t('profile.messengerLock.toast.wrongPin')),
      )
    }
  }

  const biometrieUmschalten = async (an: boolean) => {
    if (!an) {
      await useMessengerSperre.getState().biometrieAusschalten()
      toast.success(
        t('profile.messengerLock.toast.bioOff'),
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
        t('profile.messengerLock.toast.bioOn'),
      )
    } catch (fehler) {
      toast.error(
        fehlertext(
          fehler,
          t('profile.messengerLock.toast.bioFailed'),
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
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-on-surface">
                {t('profile.messengerLock.title')}
              </h2>
              <DisBadge size={14} className="hidden sm:inline-flex py-0.5 px-2" />
            </div>
            <p className="text-label-sm text-on-surface-variant">
              {eingerichtet
                ? t('profile.messengerLock.stateOn')
                : t('profile.messengerLock.stateOff')}
            </p>
          </div>
        </div>

        <div className="space-y-3 pt-2 border-t border-outline-variant/30">
          <p className="text-xs text-on-surface-variant leading-relaxed">
            {t('profile.messengerLock.explain')}
          </p>

          {eingerichtet && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-label-sm text-on-surface-variant">
              {geraetebindung
                ? t('profile.messengerLock.boundNote')
                : t('profile.messengerLock.unboundNote')}
            </div>
          )}

          {!eingerichtet && formular !== 'einrichten' && (
            <Button onClick={() => setFormular('einrichten')} disabled={laeuft}>
              {t('profile.messengerLock.setUp')}
            </Button>
          )}

          {formular === 'einrichten' && (
            <div className="space-y-3">
              <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-label-sm text-on-surface-variant leading-relaxed">
                {t('profile.messengerLock.warnLoss')}
              </div>
              <PasswordInput
                label={t('profile.messengerLock.newPin')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="new-password"
                error={
                  zuKurz
                    ? t('profile.messengerLock.tooShort', {
                        count: PIN_MINDESTLAENGE,
                      })
                    : undefined
                }
              />
              <PasswordInput
                label={t('profile.messengerLock.repeat')}
                value={pinWiederholung}
                onChange={(e) => setPinWiederholung(e.target.value)}
                autoComplete="new-password"
                error={
                  passtNicht
                    ? t('profile.messengerLock.mismatch')
                    : undefined
                }
              />
              <div className="flex gap-2">
                <Button
                  onClick={einrichten}
                  disabled={laeuft || pin.length < PIN_MINDESTLAENGE || pin !== pinWiederholung}
                >
                  {laeuft
                    ? t('profile.messengerLock.working')
                    : t('profile.messengerLock.setUp')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          )}

          {eingerichtet && formular === 'keines' && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setFormular('aendern')}>
                {t('profile.messengerLock.change')}
              </Button>
              <Button variant="ghost" onClick={() => setFormular('abschalten')}>
                {t('profile.messengerLock.turnOff')}
              </Button>
            </div>
          )}

          {formular === 'aendern' && (
            <div className="space-y-3">
              <PasswordInput
                label={t('profile.messengerLock.currentPin')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="current-password"
              />
              <PasswordInput
                label={t('profile.messengerLock.newPin')}
                value={neuerPin}
                onChange={(e) => setNeuerPin(e.target.value)}
                autoComplete="new-password"
              />
              <PasswordInput
                label={t('profile.messengerLock.repeat')}
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
                  {t('profile.messengerLock.change')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          )}

          {formular === 'abschalten' && (
            <div className="space-y-3">
              <PasswordInput
                label={t('profile.messengerLock.currentPin')}
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                autoComplete="current-password"
              />
              <div className="flex gap-2">
                <Button variant="destructive" onClick={abschalten} disabled={laeuft || !pin}>
                  {t('profile.messengerLock.turnOff')}
                </Button>
                <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                  {t('common.cancel')}
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
              {t('profile.messengerLock.bioTitle')}
            </h2>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          {!biometrieMoeglich ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant leading-relaxed">
              {t('profile.messengerLock.bioUnavailable')}
            </div>
          ) : !eingerichtet ? (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t('profile.messengerLock.bioNeedsPin')}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <span className="text-xs font-medium text-on-surface">
                    {t('profile.messengerLock.bioSwitch')}
                  </span>
                  <p className="text-label-sm text-on-surface-variant">
                    {t('profile.messengerLock.bioHint')}
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
                    label={t('profile.messengerLock.currentPin')}
                    value={pin}
                    onChange={(e) => setPin(e.target.value)}
                    autoComplete="current-password"
                  />
                  <div className="flex gap-2">
                    <Button onClick={biometrieEinrichten} disabled={laeuft || !pin}>
                      {t('profile.messengerLock.bioSetUp')}
                    </Button>
                    <Button variant="ghost" onClick={formularSchliessen} disabled={laeuft}>
                      {t('common.cancel')}
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
              {t('profile.messengerLock.autoTitle')}
            </h2>
            <p className="text-label-sm text-on-surface-variant">
              {t('profile.messengerLock.autoSubtitle')}
            </p>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          {!eingerichtet && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              {t('profile.messengerLock.autoNeedsPin')}
            </div>
          )}

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <label className="text-xs font-medium text-on-surface">
              {t('profile.messengerLock.afterIdle')}
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
                {t('profile.messengerLock.onBlur')}
              </span>
              <p className="text-label-sm text-on-surface-variant">
                {t('profile.messengerLock.onBlurHint')}
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
          {t('profile.messengerLock.footnote')}
        </p>
      </div>
    </div>
  )
}
