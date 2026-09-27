/**
 * Passkeys — zwei verschiedene Dinge unter einem Namen.
 *
 * 1. **Zweiter Faktor gegenüber dem Server** (`passkeyAnlegen`,
 *    `passkeyBestaetigen`, `passkeyNachweis`): echtes WebAuthn. Der Server
 *    stellt eine Einmal-Challenge, der Authenticator unterschreibt sie, der
 *    Server prüft die Unterschrift gegen den gespeicherten Schlüssel
 *    (`backend/services/passkey_service.py`).
 *
 * 2. **Lokale Entsperrung** (`verifyPasskey`): Windows Hello, BiometricPrompt
 *    oder eine WebAuthn-Abfrage ohne Server. Sie belegt nur *diesem Gerät*,
 *    dass jemand davorsitzt — etwa für den Tresor. **Nie** als Nachweis an
 *    den Server schicken: bis 09/2026 ging nach ihr `passkey_verified: true`
 *    hinaus, und der Server glaubte es. Das Feld konnte jeder selbst setzen.
 */

import { api } from '@/api/client'
import i18n from '@/i18n'
import { pruefeBiometrieVerfuegbar, verifiziereBiometrie } from '@/desktop/tauri'

async function checkAndroidBiometric(): Promise<boolean> {
  try {
    const { checkStatus } = await import('@tauri-apps/plugin-biometric')
    const status = await checkStatus()
    return !!status.isAvailable
  } catch {
    return false
  }
}

async function promptAndroidBiometric(title?: string): Promise<boolean> {
  try {
    const { authenticate } = await import('@tauri-apps/plugin-biometric')
    await authenticate(title || 'Authentifizierung', {
      allowDeviceCredential: true,
    })
    return true
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (
      msg.includes('cancel') ||
      msg.includes('Cancel') ||
      msg.includes('abgebrochen') ||
      msg.includes('User canceled') ||
      msg.includes('NegativeButton')
    ) {
      throw new Error(i18n.t('auth.passkeyErrors.biometricCancelled'))
    }
    return false
  }
}

/**
 * Lokale Entsperrung: belegt nur diesem Gerät, dass jemand davorsitzt.
 *
 * Kein Nachweis für den Server — dafür `passkeyNachweis`. Verwendet für die
 * Tresor-Entsperrung.
 */
export async function verifyPasskey(title = i18n.t('auth.confirmIdentity')): Promise<boolean> {
  // 1. In Tauri / Desktop: Nutze native Windows Hello API
  try {
    const isWindowsHelloAvailable = await pruefeBiometrieVerfuegbar()
    if (isWindowsHelloAvailable) {
      return await verifiziereBiometrie(title)
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg.includes('abgebrochen') || msg.includes('Canceled') || msg.includes('Fehler')) {
      throw new Error(i18n.t('auth.passkeyErrors.biometricCancelled'))
    }
    return false
  }

  // 2. In Tauri / Mobile (Android): Nutze BiometricPrompt
  try {
    const isAndroidAvailable = await checkAndroidBiometric()
    if (isAndroidAvailable) {
      return await promptAndroidBiometric(title)
    }
  } catch (err: unknown) {
    throw err
  }

  // 3. WebAuthn Plattform-Authenticator (Fail-Closed)
  if (typeof window === 'undefined' || !window.PublicKeyCredential || !navigator.credentials) {
    return false
  }

  try {
    const challenge = new Uint8Array(32)
    window.crypto.getRandomValues(challenge)

    const credential = await navigator.credentials.get({
      publicKey: {
        challenge,
        timeout: 60000,
        userVerification: 'required',
        rpId: window.location.hostname || undefined,
      },
    })
    return !!credential
  } catch (err) {
    const errorName = (err && typeof err === 'object' && 'name' in err) ? String(err.name) : ''
    const errorMsg = err instanceof Error ? err.message : String(err)
    if (
      errorName === 'NotAllowedError' ||
      errorName === 'AbortError' ||
      errorMsg.includes('NotAllowedError') ||
      errorMsg.toLowerCase().includes('cancel') ||
      errorMsg.toLowerCase().includes('abort')
    ) {
      throw new Error(i18n.t('auth.passkeyErrors.biometricCancelled'))
    }
    return false
  }
}

// ── Zweiter Faktor gegenüber dem Server (WebAuthn) ─────────────────────────

/** Optionen für `navigator.credentials.create()`, Binärfelder als Base64url. */
export interface PasskeyAnlageOptionen {
  challenge: string
  rp: { name: string; id: string }
  user: { id: string; name: string; displayName: string }
  pubKeyCredParams: { type: 'public-key'; alg: number }[]
  timeout?: number
  attestation?: AttestationConveyancePreference
  authenticatorSelection?: AuthenticatorSelectionCriteria
  excludeCredentials?: { type: 'public-key'; id: string; transports?: string[] }[]
}

/** Optionen für `navigator.credentials.get()`, Binärfelder als Base64url. */
export interface PasskeyBestaetigungsOptionen {
  challenge: string
  rpId: string
  timeout?: number
  userVerification?: UserVerificationRequirement
  allowCredentials: { type: 'public-key'; id: string; transports?: string[] }[]
}

/** Was der Server als Nachweis annimmt (`schemas/passkey.py`). */
export interface PasskeyNachweis {
  id: string
  rawId: string
  type: 'public-key'
  response: {
    clientDataJSON: string
    authenticatorData: string
    signature: string
    userHandle: string | null
  }
}

export interface PasskeyAnlage {
  id: string
  rawId: string
  type: 'public-key'
  response: {
    clientDataJSON: string
    authenticatorData: string
    publicKey: string
    publicKeyAlgorithm: number
    transports: string[]
  }
}

/** Wofür ein Nachweis gilt — der Server bindet die Challenge daran. */
export type PasskeyZweck =
  | '2fa_disable'
  | 'device_pairing'
  | 'e2ee_reset'
  | 'password_change'
  | 'email_change'
  | 'account_delete'
  | 'data_export'

function nachB64url(puffer: ArrayBuffer): string {
  const bytes = new Uint8Array(puffer)
  let text = ''
  for (const b of bytes) text += String.fromCharCode(b)
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function ausB64url(text: string): ArrayBuffer {
  const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4)
  const roh = atob(b64)
  const bytes = new Uint8Array(roh.length)
  for (let i = 0; i < roh.length; i++) bytes[i] = roh.charCodeAt(i)
  return bytes.buffer
}

/** Ob dieser Browser bzw. dieses WebView überhaupt WebAuthn kann. */
export function webauthnVerfuegbar(): boolean {
  return typeof window !== 'undefined' && Boolean(window.PublicKeyCredential && navigator.credentials)
}

function abbruchFehler(err: unknown): Error {
  const name = err && typeof err === 'object' && 'name' in err ? String((err as { name: unknown }).name) : ''
  if (name === 'NotAllowedError' || name === 'AbortError') {
    return new Error(i18n.t('auth.passkeyErrors.cancelled'))
  }
  if (name === 'InvalidStateError') {
    return new Error(i18n.t('auth.passkeyErrors.alreadyRegistered'))
  }
  if (name === 'SecurityError') {
    return new Error(i18n.t('auth.passkeyErrors.wrongOrigin'))
  }
  return err instanceof Error ? err : new Error(String(err))
}

/** Legt einen Passkey an; die Antwort geht an `/auth/2fa/passkey/enable`. */
export async function passkeyAnlegen(optionen: PasskeyAnlageOptionen): Promise<PasskeyAnlage> {
  if (!webauthnVerfuegbar()) throw new Error(i18n.t('auth.passkeyErrors.unsupported'))
  let credential: PublicKeyCredential | null
  try {
    credential = (await navigator.credentials.create({
      publicKey: {
        ...optionen,
        challenge: ausB64url(optionen.challenge),
        user: { ...optionen.user, id: ausB64url(optionen.user.id) },
        excludeCredentials: (optionen.excludeCredentials ?? []).map((c) => ({
          type: c.type,
          id: ausB64url(c.id),
          transports: c.transports as AuthenticatorTransport[] | undefined,
        })),
      },
    })) as PublicKeyCredential | null
  } catch (err) {
    throw abbruchFehler(err)
  }
  if (!credential) throw new Error(i18n.t('auth.passkeyErrors.noCreateResult'))
  const antwort = credential.response as AuthenticatorAttestationResponse
  // `getPublicKey()` liefert den Schlüssel als SPKI — damit braucht der Server
  // kein CBOR. Fehlt er, kann der Browser den Algorithmus nicht ausdrücken.
  const oeffentlich = antwort.getPublicKey?.()
  const authData = antwort.getAuthenticatorData?.()
  if (!oeffentlich || !authData) {
    throw new Error(i18n.t('auth.passkeyErrors.browserOutdated'))
  }
  return {
    id: credential.id,
    rawId: nachB64url(credential.rawId),
    type: 'public-key',
    response: {
      clientDataJSON: nachB64url(antwort.clientDataJSON),
      authenticatorData: nachB64url(authData),
      publicKey: nachB64url(oeffentlich),
      publicKeyAlgorithm: antwort.getPublicKeyAlgorithm(),
      transports: antwort.getTransports?.() ?? [],
    },
  }
}

/** Lässt die Server-Challenge unterschreiben. */
export async function passkeyBestaetigen(optionen: PasskeyBestaetigungsOptionen): Promise<PasskeyNachweis> {
  if (!webauthnVerfuegbar()) throw new Error(i18n.t('auth.passkeyErrors.unsupported'))
  let credential: PublicKeyCredential | null
  try {
    credential = (await navigator.credentials.get({
      publicKey: {
        challenge: ausB64url(optionen.challenge),
        rpId: optionen.rpId,
        timeout: optionen.timeout,
        userVerification: optionen.userVerification,
        allowCredentials: optionen.allowCredentials.map((c) => ({
          type: c.type,
          id: ausB64url(c.id),
          transports: c.transports as AuthenticatorTransport[] | undefined,
        })),
      },
    })) as PublicKeyCredential | null
  } catch (err) {
    throw abbruchFehler(err)
  }
  if (!credential) throw new Error(i18n.t('auth.passkeyErrors.noConfirmResult'))
  const antwort = credential.response as AuthenticatorAssertionResponse
  return {
    id: credential.id,
    rawId: nachB64url(credential.rawId),
    type: 'public-key',
    response: {
      clientDataJSON: nachB64url(antwort.clientDataJSON),
      authenticatorData: nachB64url(antwort.authenticatorData),
      signature: nachB64url(antwort.signature),
      userHandle: antwort.userHandle ? nachB64url(antwort.userHandle) : null,
    },
  }
}

/**
 * Der Nachweis vor einer geschützten Aktion: Challenge holen, unterschreiben.
 * Die Challenge gilt nur für `zweck` und nur einmal.
 */
export async function passkeyNachweis(zweck: PasskeyZweck): Promise<PasskeyNachweis> {
  const optionen = await api<PasskeyBestaetigungsOptionen>('/auth/passkey/options', {
    method: 'POST',
    body: JSON.stringify({ zweck }),
  })
  return passkeyBestaetigen(optionen)
}
