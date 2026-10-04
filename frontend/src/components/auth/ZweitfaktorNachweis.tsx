import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Fingerprint, KeyRound } from 'lucide-react'
import { Button, Input } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { inDerApp, passkeyNachweis, type PasskeyZweck, type Zweitnachweis } from '@/services/passkeyService'
import type { Zweitfaktor } from '@/types'

/** Was der Server als zweiten Faktor annimmt: der Passkey oder ein Code aus der App. */
export type FaktorNachweis = { passkey: Zweitnachweis } | { otp_code: string } | Record<string, never>

/** Vor einem neuen Faktor nimmt der Server zusätzlich einen Backup-Code an. */
export type NeuerFaktorNachweis = FaktorNachweis | { backup_code: string }

/** Ein Weg zum Nachweis: ein eingerichteter Faktor oder ein Backup-Code. */
export type Nachweisweg = Zweitfaktor | 'backup'

/**
 * Der zweite Faktor vor einer geschützten Aktion.
 *
 * Ein Konto kann Passkeys und die Authenticator-App zugleich haben (seit
 * 29.09.2026). Vorgewählt ist der zuletzt genutzte Faktor, sonst der Passkey.
 * Faktoren, die das Konto nicht hat, erscheinen nie.
 *
 * Bis 04.10.2026 stand immer der Passkey vorn und der Code nur hinter einem
 * unauffälligen „Stattdessen …"-Knopf. Wer am neuen Handy einen weiteren
 * Passkey anlegen wollte, bekam nach dem Klick die Abfrage des Passkeys vom
 * PC — und kam nicht weiter, obwohl die App eingerichtet war.
 *
 * `backup`: bietet zusätzlich den Backup-Code an. Der Server nimmt ihn nur
 * für einen neuen Faktor an (Entscheidung 04.10.2026) — ein Konto nur mit
 * dem Passkey vom PC hat am neuen Handy sonst nichts in der Hand.
 */
export function useZweitfaktor({ backup = false }: { backup?: boolean } = {}) {
  const user = useAuthStore((s) => s.user)
  const methoden: Zweitfaktor[] = user?.two_factor_enabled ? (user.two_factor_methods ?? []) : []
  const wege: Nachweisweg[] = backup && methoden.length > 0 ? [...methoden, 'backup'] : methoden
  const zuletzt = methoden.find((m) => m === user?.two_factor_last_method)
  const [gewaehlt, setWahl] = useState<Nachweisweg | null>(null)
  const [code, setCode] = useState('')
  const wahl = gewaehlt && wege.includes(gewaehlt) ? gewaehlt : (zuletzt ?? methoden[0] ?? null)

  /** Holt den Nachweis beim Absenden. Die Passkey-Abfrage muss nah am Klick liegen. */
  const nachweis = async (zweck: PasskeyZweck): Promise<FaktorNachweis> => {
    if (wahl === 'passkey') return { passkey: await passkeyNachweis(zweck) }
    if (wahl === 'totp') return { otp_code: code.trim() }
    return {}
  }

  /** Wie `nachweis`, aber vor einem neuen Faktor; dort zählt auch der Backup-Code. */
  const nachweisNeuerFaktor = async (zweck: PasskeyZweck): Promise<NeuerFaktorNachweis> =>
    wahl === 'backup' ? { backup_code: code.trim() } : nachweis(zweck)

  return {
    methoden,
    wege,
    wahl,
    setWahl,
    code,
    setCode,
    nachweis,
    nachweisNeuerFaktor,
    /** Ob der Nachweis vollständig ist, bevor abgeschickt wird. */
    bereit:
      wahl === 'totp'
        ? code.trim().length === 6
        : wahl === 'backup'
          ? code.replace(/[^A-Za-z0-9]/g, '').length === 8
          : true,
  }
}

export type Zweitfaktorzustand = ReturnType<typeof useZweitfaktor>

const WEG_NAME: Record<Nachweisweg, string> = {
  passkey: 'auth.zweitfaktor.wegPasskey',
  totp: 'auth.zweitfaktor.wegTotp',
  backup: 'auth.useBackupCode',
}

/** Die Wahl zwischen den Wegen, sichtbar nebeneinander; nur wenn es mehr als einen gibt. */
export function FaktorWechsel({ faktor, disabled }: { faktor: Zweitfaktorzustand; disabled?: boolean }) {
  const { t } = useTranslation()
  if (faktor.wege.length < 2) return null
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label={t('auth.zweitfaktor.wahl')}>
      {faktor.wege.map((m) => {
        const aktiv = m === faktor.wahl
        return (
          <Button
            key={m}
            type="button"
            variant={aktiv ? 'secondary' : 'ghost'}
            size="sm"
            aria-pressed={aktiv}
            disabled={disabled}
            onClick={() => {
              if (aktiv) return
              faktor.setWahl(m)
              faktor.setCode('')
            }}
          >
            {m === 'passkey' ? (
              <Fingerprint className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {t(WEG_NAME[m])}
          </Button>
        )
      })}
    </div>
  )
}

interface FeldProps {
  faktor: Zweitfaktorzustand
  id: string
  label?: string
  disabled?: boolean
}

/**
 * Die Wahl des Faktors und das Feld dazu: ein Codefeld für die App, beim
 * Passkey nur der Hinweis, was nach dem Klick passiert. Ohne 2FA zeigt es
 * nichts; das Passwort fragt die Seite selbst.
 */
export function ZweitfaktorFeld({ faktor, id, label, disabled }: FeldProps) {
  const { t } = useTranslation()
  if (!faktor.wahl) return null
  return (
    <div className="space-y-2">
      <FaktorWechsel faktor={faktor} disabled={disabled} />
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
      ) : faktor.wahl === 'backup' ? (
        <Input
          id={id}
          label={t('auth.backupCode')}
          value={faktor.code}
          onChange={(e) => faktor.setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 9))}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          placeholder="XXXX-XXXX"
          required
          disabled={disabled}
        />
      ) : (
        <p className="text-xs text-on-surface-variant">
          {inDerApp() ? t('auth.zweitfaktor.passkeyHintApp') : t('auth.zweitfaktor.passkeyHint')}
        </p>
      )}
    </div>
  )
}
