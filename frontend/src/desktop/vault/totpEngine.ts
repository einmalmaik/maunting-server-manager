/**
 * RFC 6238 TOTP Engine für den integrierten Authenticator.
 * Unterstützt HMAC-SHA1, HMAC-SHA256, HMAC-SHA512 mit variablen Stellen und Zeitfenstern.
 */

const BASE32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'

export function base32Decode(input: string): Uint8Array {
  const cleaned = input.toUpperCase().replace(/[\s=-]/g, '')
  if (cleaned.length === 0) {
    throw new Error('Leeres Base32 TOTP-Secret')
  }

  let bits = ''
  for (let i = 0; i < cleaned.length; i++) {
    const val = BASE32_ALPHABET.indexOf(cleaned.charAt(i))
    if (val === -1) {
      throw new Error(`Ungültiges Base32-Zeichen '${cleaned.charAt(i)}' an Position ${i}`)
    }
    bits += val.toString(2).padStart(5, '0')
  }

  const bytes = new Uint8Array(Math.floor(bits.length / 8))
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(bits.substr(i * 8, 8), 2)
  }
  return bytes
}

export type TotpAlgorithm = 'SHA-1' | 'SHA-256' | 'SHA-512' | 'SHA1' | 'SHA256' | 'SHA512'

export interface TotpGenerateOptions {
  period?: number
  digits?: number
  algorithm?: TotpAlgorithm
  timestampMs?: number
}

function normalizeHashName(algo?: TotpAlgorithm): 'SHA-1' | 'SHA-256' | 'SHA-512' {
  if (!algo) return 'SHA-1'
  const upper = algo.toUpperCase().replace('-', '')
  if (upper === 'SHA256') return 'SHA-256'
  if (upper === 'SHA512') return 'SHA-512'
  return 'SHA-1'
}

/**
 * Berechnet das aktuelle TOTP-Token für ein Base32-Secret gemäß RFC 6238 / RFC 4226.
 */
export async function generateTotpCode(
  secretBase32: string,
  optionsOrStep: number | TotpGenerateOptions = 30,
  timestampMsFallback = Date.now(),
): Promise<string> {
  let period = 30
  let digits = 6
  let algorithm: 'SHA-1' | 'SHA-256' | 'SHA-512' = 'SHA-1'
  let timestampMs = timestampMsFallback

  if (typeof optionsOrStep === 'number') {
    period = optionsOrStep
  } else if (optionsOrStep && typeof optionsOrStep === 'object') {
    if (optionsOrStep.period) period = optionsOrStep.period
    if (optionsOrStep.digits) digits = optionsOrStep.digits
    if (optionsOrStep.algorithm) algorithm = normalizeHashName(optionsOrStep.algorithm)
    if (optionsOrStep.timestampMs !== undefined) timestampMs = optionsOrStep.timestampMs
  }

  const keyBytes = base32Decode(secretBase32)
  if (keyBytes.byteLength === 0) {
    throw new Error('Ungültiges Base32 TOTP-Secret')
  }

  const epochSeconds = Math.floor(timestampMs / 1000)
  const counter = Math.floor(epochSeconds / period)

  // 8-Byte Big-Endian Counter Buffer
  const counterBuffer = new ArrayBuffer(8)
  const counterView = new DataView(counterBuffer)
  // Bei 32-Bit Zeit-Überläufen in der fernen Zukunft:
  const high = Math.floor(counter / 0x100000000)
  const low = counter >>> 0
  counterView.setUint32(0, high, false)
  counterView.setUint32(4, low, false)

  const rawKeyBuffer = new ArrayBuffer(keyBytes.byteLength)
  new Uint8Array(rawKeyBuffer).set(keyBytes)

  const key = await window.crypto.subtle.importKey(
    'raw',
    rawKeyBuffer,
    { name: 'HMAC', hash: { name: algorithm } },
    false,
    ['sign'],
  )

  const signature = await window.crypto.subtle.sign('HMAC', key, counterBuffer)
  const hmac = new Uint8Array(signature)

  // Dynamic Truncation (RFC 4226 Section 5.4)
  const offset = hmac[hmac.length - 1] & 0x0f
  const binaryCode =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff)

  const modulo = 10 ** digits
  const token = (binaryCode % modulo).toString().padStart(digits, '0')
  return token
}

/**
 * Gibt die verbleibenden Sekunden im aktuellen Zeitfenster zurück.
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
  algorithm?: 'SHA-1' | 'SHA-256' | 'SHA-512'
  digits?: number
  period?: number
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
      if (/^[A-Z2-7]{16,128}$/.test(cleanSecret)) {
        return { secret: cleanSecret }
      }
      return null
    }

    const url = new URL(trimmed)
    const secret = url.searchParams.get('secret')
    if (!secret) return null

    let issuer = url.searchParams.get('issuer') || undefined
    let label = decodeURIComponent(url.pathname.replace(/^\/totp\/?/, ''))

    if (label.includes(':')) {
      const parts = label.split(':')
      if (!issuer) {
        issuer = parts[0].trim()
      }
      label = parts[1].trim()
    }

    const algoParam = url.searchParams.get('algorithm')
    const digitsParam = url.searchParams.get('digits')
    const periodParam = url.searchParams.get('period')

    const algorithm = algoParam ? normalizeHashName(algoParam as TotpAlgorithm) : undefined
    const digits = digitsParam ? parseInt(digitsParam, 10) : undefined
    const period = periodParam ? parseInt(periodParam, 10) : undefined

    return {
      secret: secret.trim().toUpperCase(),
      issuer,
      label,
      algorithm,
      digits: digits && !isNaN(digits) ? digits : undefined,
      period: period && !isNaN(period) ? period : undefined,
    }
  } catch {
    return null
  }
}
