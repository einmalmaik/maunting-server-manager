/**
 * RFC 6238 TOTP Engine für den integrierten Authenticator.
 * Berechnet 6-stellige Einmal-Passwörter auf Basis von HMAC-SHA1.
 */

const BASE32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'

export function base32Decode(input: string): Uint8Array {
  const cleaned = input.toUpperCase().replace(/[\s=-]/g, '')
  let bits = ''
  for (let i = 0; i < cleaned.length; i++) {
    const val = BASE32_ALPHABET.indexOf(cleaned.charAt(i))
    if (val === -1) continue
    bits += val.toString(2).padStart(5, '0')
  }

  const bytes = new Uint8Array(Math.floor(bits.length / 8))
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(bits.substr(i * 8, 8), 2)
  }
  return bytes
}

/**
 * Berechnet das aktuelle 6-stellige TOTP-Token für ein Secret.
 */
export async function generateTotpCode(
  secretBase32: string,
  timeStepSeconds = 30,
  timestampMs = Date.now(),
): Promise<string> {
  const keyBytes = base32Decode(secretBase32)
  if (keyBytes.byteLength === 0) {
    throw new Error('Ungültiges Base32 TOTP-Secret')
  }

  const epochSeconds = Math.floor(timestampMs / 1000)
  const counter = Math.floor(epochSeconds / timeStepSeconds)

  // 8-Byte Big-Endian Counter
  const counterBuffer = new ArrayBuffer(8)
  const counterView = new DataView(counterBuffer)
  counterView.setUint32(4, counter, false) // Counter passt in 32 Bit für aktuelle Zeit

  const rawKeyBuffer = new ArrayBuffer(keyBytes.byteLength)
  new Uint8Array(rawKeyBuffer).set(keyBytes)

  const key = await window.crypto.subtle.importKey(
    'raw',
    rawKeyBuffer,
    { name: 'HMAC', hash: { name: 'SHA-1' } },
    false,
    ['sign'],
  )

  const signature = await window.crypto.subtle.sign('HMAC', key, counterBuffer)
  const hmac = new Uint8Array(signature)

  // Dynamic Truncation
  const offset = hmac[hmac.length - 1] & 0x0f
  const code =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff)

  const token = (code % 1000000).toString().padStart(6, '0')
  return token
}

/**
 * Gibt die verbleibenden Sekunden im aktuellen 30-Sekunden-Fenster zurück.
 */
export function getTotpSecondsRemaining(step = 30): number {
  const currentSeconds = Math.floor(Date.now() / 1000)
  const rem = step - (currentSeconds % step)
  return rem === 0 ? step : rem
}

export interface ParsedOtpAuth {
  secret: string
  issuer?: string
  label?: string
}

/**
 * Parst einen otpauth://totp URI aus einem QR-Code oder Eingabefeld.
 */
export function parseOtpauthUri(rawUri: string): ParsedOtpAuth | null {
  try {
    const trimmed = rawUri.trim()
    if (!trimmed.startsWith('otpauth://totp/')) {
      // Eventuell ist es nur ein roher Base32-Key
      const cleanSecret = trimmed.replace(/[\s-]/g, '').toUpperCase()
      if (/^[A-Z2-7]{16,64}$/.test(cleanSecret)) {
        return { secret: cleanSecret }
      }
      return null
    }

    const url = new URL(trimmed)
    const secret = url.searchParams.get('secret')
    if (!secret) return null

    let issuer = url.searchParams.get('issuer') || undefined
    let label = decodeURIComponent(url.pathname.replace(/^\/totp\//, ''))

    if (label.includes(':')) {
      const parts = label.split(':')
      if (!issuer) {
        issuer = parts[0].trim()
      }
      label = parts[1].trim()
    }

    return {
      secret: secret.trim().toUpperCase(),
      issuer,
      label,
    }
  } catch {
    return null
  }
}
