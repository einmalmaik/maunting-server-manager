/**
 * Datenschutzfreundliche Passwort-Prüfung gegen bekannte Datenlecks.
 * Verwendet das K-Anonymitäts-Modell (Have I Been Pwned API):
 * - Nur die ersten 5 Hex-Zeichen des SHA-1 Hashes verlassen das Endgerät.
 * - Minimum 6 Zeichen vor Netzwerkaufruf (SEC-10).
 * - Debouncing von mind. 400 ms gegen Keystroke-Leaks (SEC-10).
 * - Das Klartext-Passwort und der vollständige Hash bleiben zu 100 % lokal.
 */

export interface LeakCheckResult {
  isLeaked: boolean
  count: number
  checked: boolean
}

// In-Memory Cache für bereits geprüfte Hashes innerhalb der Sitzung
const prefixCache = new Map<string, string>()

export const MIN_LEAK_CHECK_PASSWORD_LENGTH = 6
export const DEFAULT_LEAK_CHECK_DEBOUNCE_MS = 400

/**
 * Führt eine direkte K-Anonymitätsprüfung gegen die HIBP Range-API aus.
 * Gibt sofort { isLeaked: false, count: 0, checked: false } zurück, falls das Passwort < 6 Zeichen ist.
 */
export async function checkPasswordLeak(password: string): Promise<LeakCheckResult> {
  if (!password || password.length < MIN_LEAK_CHECK_PASSWORD_LENGTH) {
    return { isLeaked: false, count: 0, checked: false }
  }

  try {
    const encoder = new TextEncoder()
    const data = encoder.encode(password)
    const hashBuffer = await crypto.subtle.digest('SHA-1', data)
    const hashArray = Array.from(new Uint8Array(hashBuffer))
    const hashHex = hashArray.map((b) => b.toString(16).padStart(2, '0')).join('').toUpperCase()

    const prefix = hashHex.slice(0, 5)
    const suffix = hashHex.slice(5)

    let responseText = prefixCache.get(prefix)

    if (!responseText) {
      // Abruf mit kurzem Timeout (3s), damit die UI niemals blockiert
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 3000)

      try {
        const res = await fetch(`https://api.pwnedpasswords.com/range/${prefix}`, {
          signal: controller.signal,
          headers: {
            'Add-Padding': 'true',
          },
        })

        if (!res.ok) {
          return { isLeaked: false, count: 0, checked: false }
        }

        responseText = await res.text()
        prefixCache.set(prefix, responseText)
      } finally {
        clearTimeout(timeoutId)
      }
    }

    // Zeilen durchsuchen: Format "SUFFIX:COUNT"
    const lines = responseText.split('\n')
    for (const line of lines) {
      const cleanLine = line.trim()
      if (cleanLine.startsWith(suffix)) {
        const parts = cleanLine.split(':')
        const count = parseInt(parts[1] || '0', 10)
        return { isLeaked: count > 0, count, checked: true }
      }
    }

    return { isLeaked: false, count: 0, checked: true }
  } catch {
    // Offline oder Netzwerk-Fehler -> Leise fehlschlagen, Benutzer nicht blockieren
    return { isLeaked: false, count: 0, checked: false }
  }
}

/**
 * Erzeugt eine gedebouncte Leak-Check Funktion für Eingabefelder (mind. 400ms Inaktivität).
 */
export function createDebouncedLeakChecker(
  onResult: (res: LeakCheckResult | null) => void,
  delayMs = DEFAULT_LEAK_CHECK_DEBOUNCE_MS,
): (password: string) => void {
  let timerId: ReturnType<typeof setTimeout> | null = null

  return (password: string) => {
    if (timerId) {
      clearTimeout(timerId)
      timerId = null
    }

    if (!password || password.length < MIN_LEAK_CHECK_PASSWORD_LENGTH) {
      onResult(null)
      return
    }

    timerId = setTimeout(async () => {
      const result = await checkPasswordLeak(password)
      onResult(result)
    }, Math.max(delayMs, DEFAULT_LEAK_CHECK_DEBOUNCE_MS))
  }
}
