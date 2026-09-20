/**
 * Was statt des Verlaufs dasteht, solange der Messenger zu ist.
 *
 * Bewusst nüchtern: hier steht kein „Zugriff verweigert" und kein Schloss in
 * Rot. Es ist der eigene Messenger, nicht ein Alarm — wer hier landet, hat den
 * PIN selbst eingerichtet und will ihn eingeben, nicht erschreckt werden.
 *
 * Der Schirm ersetzt den Inhalt, er legt sich nicht darüber. Ein Overlay über
 * einem gerenderten Verlauf wäre genau die Art Schutz, die bei der ersten
 * Entwicklerkonsole verschwindet.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Fingerprint, Lock } from 'lucide-react'

import { PasswordInput } from '@/components/ui/PasswordInput'
import { Button } from '@/Singra/UI'
import { useMessengerSperre } from '@/services/messengerSperre'

export function MessengerSperrschirm() {
  const { t } = useTranslation()
  const laeuft = useMessengerSperre((s) => s.laeuft)
  const fehler = useMessengerSperre((s) => s.fehler)
  const biometrieAktiv = useMessengerSperre((s) => s.biometrieAktiv)
  const gesperrtBis = useMessengerSperre((s) => s.gesperrtBis)

  const [pin, setPin] = useState('')
  const [restSekunden, setRestSekunden] = useState(0)
  const eingabe = useRef<HTMLInputElement>(null)

  useEffect(() => {
    eingabe.current?.focus()
  }, [])

  // Die Wartezeit läuft sichtbar ab. Eine Schaltfläche, die ohne Begründung
  // nichts tut, ist schlimmer als eine, die sagt, wie lange noch.
  useEffect(() => {
    if (gesperrtBis <= Date.now()) {
      setRestSekunden(0)
      return
    }
    const takt = setInterval(() => {
      const rest = Math.ceil((gesperrtBis - Date.now()) / 1000)
      setRestSekunden(rest > 0 ? rest : 0)
      if (rest <= 0) clearInterval(takt)
    }, 250)
    setRestSekunden(Math.ceil((gesperrtBis - Date.now()) / 1000))
    return () => clearInterval(takt)
  }, [gesperrtBis])

  const wartet = restSekunden > 0

  const entsperren = async () => {
    if (!pin || wartet) return
    const offen = await useMessengerSperre.getState().entsperren(pin)
    if (offen) setPin('')
  }

  const perFinger = async () => {
    if (wartet) return
    await useMessengerSperre.getState().entsperrenMitBiometrie()
  }

  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="msm-card w-full max-w-sm p-6 space-y-5">
        <div className="flex flex-col items-center text-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <Lock className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-on-surface">
              {t('profile.messengerLock.screenTitle')}
            </h1>
            <p className="mt-1 text-xs text-on-surface-variant leading-relaxed">
              {t('profile.messengerLock.screenHint')}
            </p>
          </div>
        </div>

        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void entsperren()
          }}
        >
          <PasswordInput
            ref={eingabe}
            value={pin}
            onChange={(e) => {
              setPin(e.target.value)
              if (fehler) useMessengerSperre.getState().fehlerLoeschen()
            }}
            autoComplete="current-password"
            placeholder={t('profile.messengerLock.pinPlaceholder')}
            disabled={laeuft || wartet}
            error={
              wartet
                ? t('profile.messengerLock.waiting', {
                    count: restSekunden,
                  })
                : (fehler ?? undefined)
            }
          />

          <Button type="submit" className="w-full" disabled={laeuft || wartet || !pin}>
            {laeuft
              ? t('profile.messengerLock.working')
              : t('profile.messengerLock.unlock')}
          </Button>

          {biometrieAktiv && (
            <Button
              type="button"
              variant="secondary"
              className="w-full"
              onClick={() => void perFinger()}
              disabled={laeuft || wartet}
            >
              <Fingerprint className="h-4 w-4" />
              {t('profile.messengerLock.useBiometrics')}
            </Button>
          )}
        </form>

        <p className="text-label-sm text-on-surface-variant text-center leading-relaxed">
          {t('profile.messengerLock.screenFootnote')}
        </p>
      </div>
    </div>
  )
}
