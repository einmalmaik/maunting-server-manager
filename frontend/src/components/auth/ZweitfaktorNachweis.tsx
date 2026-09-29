import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Input } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { inDerApp, passkeyNachweis, type PasskeyZweck, type Zweitnachweis } from '@/services/passkeyService'
import type { Zweitfaktor } from '@/types'

/** Was der Server als zweiten Faktor annimmt: der Passkey oder ein Code aus der App. */
export type FaktorNachweis = { passkey: Zweitnachweis } | { otp_code: string } | Record<string, never>

/**
 * Der zweite Faktor vor einer geschützten Aktion.
 *
 * Ein Konto kann Passkeys und die Authenticator-App zugleich haben (seit
 * 29.09.2026). Angeboten wird zuerst der Passkey, per Wechsel die App.
 * Faktoren, die das Konto nicht hat, erscheinen nie.
 */
export function useZweitfaktor() {
  const user = useAuthStore((s) => s.user)
  const methoden: Zweitfaktor[] = user?.two_factor_enabled ? (user.two_factor_methods ?? []) : []
  const [gewaehlt, setWahl] = useState<Zweitfaktor | null>(null)
  const [code, setCode] = useState('')
  const wahl = gewaehlt && methoden.includes(gewaehlt) ? gewaehlt : (methoden[0] ?? null)

  /** Holt den Nachweis beim Absenden. Die Passkey-Abfrage muss nah am Klick liegen. */
  const nachweis = async (zweck: PasskeyZweck): Promise<FaktorNachweis> => {
    if (wahl === 'passkey') return { passkey: await passkeyNachweis(zweck) }
    if (wahl === 'totp') return { otp_code: code.trim() }
    return {}
  }

  return {
    methoden,
    wahl,
    setWahl,
    code,
    setCode,
    nachweis,
    /** Ob der Nachweis vollständig ist, bevor abgeschickt wird. */
    bereit: wahl !== 'totp' || code.trim().length === 6,
  }
}

export type Zweitfaktorzustand = ReturnType<typeof useZweitfaktor>

/** Wechsel zum anderen Faktor, nur wenn das Konto beide hat. */
export function FaktorWechsel({ faktor, disabled }: { faktor: Zweitfaktorzustand; disabled?: boolean }) {
  const { t } = useTranslation()
  const andere = faktor.methoden.find((m) => m !== faktor.wahl)
  if (!andere) return null
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={disabled}
      onClick={() => {
        faktor.setWahl(andere)
        faktor.setCode('')
      }}
    >
      {andere === 'passkey' ? t('auth.zweitfaktor.usePasskey') : t('auth.zweitfaktor.useCode')}
    </Button>
  )
}

interface FeldProps {
  faktor: Zweitfaktorzustand
  id: string
  label?: string
  disabled?: boolean
}

/**
 * Das Feld zum gewählten Faktor: ein Codefeld für die App, beim Passkey nur
 * der Hinweis, was nach dem Klick passiert. Ohne 2FA zeigt es nichts; das
 * Passwort fragt die Seite selbst.
 */
export function ZweitfaktorFeld({ faktor, id, label, disabled }: FeldProps) {
  const { t } = useTranslation()
  if (!faktor.wahl) return null
  return (
    <div className="space-y-1">
      {faktor.wahl === 'totp' ? (
        <Input
          id={id}
          label={label ?? t('auth.zweitfaktor.codeLabel')}
          value={faktor.code}
          onChange={(e) => faktor.setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="000000"
          required
          disabled={disabled}
        />
      ) : (
        <p className="text-xs text-on-surface-variant">
          {inDerApp() ? t('auth.zweitfaktor.passkeyHintApp') : t('auth.zweitfaktor.passkeyHint')}
        </p>
      )}
      <FaktorWechsel faktor={faktor} disabled={disabled} />
    </div>
  )
}
