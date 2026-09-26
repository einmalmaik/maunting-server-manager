/**
 * Universeller Passkey- & Biometrie-Service für Web und Tauri.
 *
 * Unterstützt:
 * 1. Tauri Desktop (Windows): Native Windows Hello API über Tauri-Kanal.
 * 2. Tauri Mobile (Android): Native BiometricPrompt API über `@tauri-apps/plugin-biometric`.
 * 3. Web-Browser (Desktop & Mobile): W3C WebAuthn (`navigator.credentials.create` / `.get`)
 *    mit Platform-Authentikatoren (Touch ID, Face ID, Windows Hello, Android-Fingerabdruck, PIN).
 */

import { pruefeBiometrieVerfuegbar, verifiziereBiometrie } from '@/desktop/tauri'

export interface PasskeyRegisterResult {
  success: boolean
  isTauri: boolean
  credentialId?: string
}

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
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return false
  }
}

/**
 * Prüft, ob Passkey bzw. biometrische Authentifizierung auf dieser Plattform verfügbar ist.
 */
export async function isPasskeyAvailable(): Promise<boolean> {
  // 1. Tauri Desktop (Windows Hello)
  try {
    if (await pruefeBiometrieVerfuegbar()) {
      return true
    }
  } catch {}

  // 2. Tauri Mobile (Android Biometrics)
  try {
    if (await checkAndroidBiometric()) {
      return true
    }
  } catch {}

  // 3. Web Platform Authenticator (WebAuthn)
  if (
    typeof window !== 'undefined' &&
    window.PublicKeyCredential &&
    typeof window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function'
  ) {
    try {
      const available = await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
      if (available) return true
    } catch {}
  }

  // Fallback: WebAuthn API grundsätzlich vorhanden
  return typeof window !== 'undefined' && Boolean(window.PublicKeyCredential && navigator.credentials)
}

/**
 * Registriert einen neuen Passkey auf diesem Gerät (WebAuthn Platform Authenticator oder Tauri Hello).
 */
export async function registerPasskey(user: {
  id: number
  username: string
  email?: string
}): Promise<PasskeyRegisterResult> {
  // 1. Tauri Desktop / Mobile
  try {
    if (await pruefeBiometrieVerfuegbar()) {
      const verified = await verifiziereBiometrie('Passkey für 2FA registrieren')
      if (!verified) throw new Error('Biometrische Bestätigung fehlgeschlagen.')
      return { success: true, isTauri: true, credentialId: `tauri-hello-${user.id}` }
    }
  } catch (err) {
    if (err instanceof Error && err.message.includes('abgebrochen')) throw err
  }

  try {
    if (await checkAndroidBiometric()) {
      const verified = await promptAndroidBiometric('Passkey für 2FA registrieren')
      if (!verified) throw new Error('Biometrische Bestätigung fehlgeschlagen.')
      return { success: true, isTauri: true, credentialId: `tauri-android-${user.id}` }
    }
  } catch (err) {
    if (err instanceof Error && err.message.includes('abgebrochen')) throw err
  }

  // 2. Web WebAuthn Platform Authenticator
  if (typeof window === 'undefined' || !window.PublicKeyCredential || !navigator.credentials) {
    throw new Error('WebAuthn / Passkeys werden von diesem Browser nicht unterstützt.')
  }

  try {
    const challenge = new Uint8Array(32)
    window.crypto.getRandomValues(challenge)

    const userIdBytes = new TextEncoder().encode(String(user.id))

    const credential = (await navigator.credentials.create({
      publicKey: {
        challenge,
        rp: {
          name: 'Maunting Service Manager',
          id: window.location.hostname || undefined,
        },
        user: {
          id: userIdBytes,
          name: user.username,
          displayName: user.username,
        },
        pubKeyCredParams: [
          { alg: -7, type: 'public-key' }, // ES256
          { alg: -257, type: 'public-key' }, // RS256
        ],
        authenticatorSelection: {
          authenticatorAttachment: 'platform',
          userVerification: 'required',
          residentKey: 'preferred',
        },
        timeout: 60000,
      },
    })) as PublicKeyCredential | null

    if (!credential) {
      throw new Error('Passkey-Erstellung lieferte kein Ergebnis.')
    }

    return {
      success: true,
      isTauri: false,
      credentialId: credential.id,
    }
  } catch (err: unknown) {
    const errorName = (err && typeof err === 'object' && 'name' in err) ? String(err.name) : ''
    const errorMsg = err instanceof Error ? err.message : String(err)
    if (
      errorName === 'NotAllowedError' ||
      errorName === 'AbortError' ||
      errorMsg.includes('NotAllowedError') ||
      errorMsg.toLowerCase().includes('cancel') ||
      errorMsg.toLowerCase().includes('abort')
    ) {
      throw new Error('Passkey-Registrierung wurde abgebrochen.')
    }
    throw new Error(`Passkey-Registrierung fehlgeschlagen: ${errorMsg}`)
  }
}

/**
 * Führt eine schnelle 1-Klick-Passkey- bzw. Biometrie-Verifikation durch.
 *
 * Verwendbar für:
 * - 2FA-Bestätigung beim Login
 * - Freigabe von Geräten
 * - Geräte-Reset („Neu beginnen“)
 * - Tresor-Entsperrung
 */
export async function verifyPasskey(title = 'Identität bestätigen'): Promise<boolean> {
  // 1. In Tauri / Desktop: Nutze native Windows Hello API
  try {
    const isWindowsHelloAvailable = await pruefeBiometrieVerfuegbar()
    if (isWindowsHelloAvailable) {
      return await verifiziereBiometrie(title)
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg.includes('abgebrochen') || msg.includes('Canceled') || msg.includes('Fehler')) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
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
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return false
  }
}
