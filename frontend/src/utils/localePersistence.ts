/**
 * Die Sprachwahl darf nur mit Einwilligung im Speicher liegen.
 *
 * Jeder Zugriff ist abgesichert, und zwar zur sicheren Seite: Wo kein Speicher
 * lesbar ist, gibt es auch keine Einwilligung, also wird nichts abgelegt. Das
 * ist dasselbe Muster wie in `lib/audioSettings.ts`, und `i18n.ts` prüft direkt
 * darunter bereits `typeof document !== 'undefined'`.
 *
 * `localStorage` fehlt nicht nur in Node. Ein Browser mit gesperrten Cookies
 * lässt schon den Zugriff auf das Objekt mit einem `SecurityError` scheitern,
 * deshalb `try` und nicht nur `typeof`. Vorher warf diese Datei in beiden
 * Fällen. Weil `i18n.ts` sie beim Laden des Moduls aufruft, riss der Fehler
 * alles mit, was `@/i18n` importiert — darunter `services/medienKrypto.ts`,
 * dessen Tests deshalb nicht starten konnten.
 */

function leseSpeicher(schluessel: string): string | null {
  try {
    return localStorage.getItem(schluessel);
  } catch {
    return null;
  }
}

export function isLocalePersistenceAllowed(): boolean {
  const consentRaw = leseSpeicher('cookie_consent');
  if (!consentRaw) return false;
  try {
    const consent = JSON.parse(consentRaw);
    return consent.optional === true;
  } catch {
    return false;
  }
}

export function getPersistedLocale(): string | null {
  if (!isLocalePersistenceAllowed()) {
    return null;
  }
  return leseSpeicher('i18nextLng');
}

export function setPersistedLocale(locale: string): void {
  try {
    if (isLocalePersistenceAllowed()) {
      localStorage.setItem('i18nextLng', locale);
    } else {
      localStorage.removeItem('i18nextLng');
    }
  } catch {
    // Ohne Speicher ist nichts abzulegen und nichts zu löschen.
  }
}

export function clearPersistedLocale(): void {
  try {
    localStorage.removeItem('i18nextLng');
  } catch {
    // Wo nichts liegen konnte, muss nichts weg.
  }
}
