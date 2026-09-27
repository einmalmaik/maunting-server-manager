/**
 * Abfrageverlauf des SQL-Editors im PostgreSQL-Studio.
 *
 * localStorage gehört der Herkunft und nicht der Anmeldung — er überlebt das
 * Abmelden und kennt keinen Benutzer. Ein fester Name zeigt darum dem nächsten
 * Benutzer am selben Rechner die Abfragen des vorigen; im Abfragetext stehen
 * Tabellen, Spalten, die Literale aus UPDATE und INSERT und das Passwort aus
 * einem CREATE ROLE. Deshalb trägt jeder Schlüssel die Benutzerkennung und die
 * Datenbank (Panel-Datenbank oder eine Datenbank eines Servers) — ohne den
 * zweiten Teil wandert der Verlauf von einer Datenbank in die nächste, wo der
 * Benutzer vielleicht nur lesen darf.
 *
 * Bis 27.09.2026 führte das Studio seinen Verlauf daran vorbei unter
 * `msm-pg-studio:verlauf:…`, ohne Benutzer und ohne Aufräumen beim Abmelden.
 *
 * Die Datei importiert absichtlich keinen Store: authStore ruft hier auf, ein
 * Rückimport wäre ein Importzyklus.
 */
export const SQL_CONSOLE_STORAGE_PREFIX = 'msm_sql'
const ALTE_STUDIO_PRAEFIXE = ['msm-pg-studio:']

/** Schlüssel des Verlaufs, z. B. `scope` = 'panel' oder 'server-7-db-3'. */
export function sqlVerlaufSchluessel(userId: number, scope: string): string {
  return `${SQL_CONSOLE_STORAGE_PREFIX}:history:${userId}:${scope}`
}

/**
 * Liest eine Liste. Kaputter oder fremder Inhalt gilt als leer: der Verlauf ist
 * Komfort, er darf den Editor nicht lahmlegen, wenn jemand im Speicher rührt.
 */
export function readSqlConsoleEntries(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : []
  } catch {
    return []
  }
}

export function writeSqlConsoleEntries(key: string, entries: string[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(entries))
  } catch {
    // Speicher voll oder gesperrt (privates Fenster): am Verlauf hängt nichts.
  }
}

/**
 * Räumt beim Ende jeder Sitzung auf: den Verlauf aller Benutzer, dazu die
 * Altlasten — ungebundene Schlüssel aus der Zeit vor der Benutzerbindung, die
 * Favoriten der alten SQL-Konsole (das Studio kennt keine) und den Verlauf des
 * Studios unter seinem alten Namen.
 */
export function clearSqlConsoleHistory(): void {
  try {
    const zuLoeschen: string[] = []
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (!key) continue
      if (key.startsWith(SQL_CONSOLE_STORAGE_PREFIX) || ALTE_STUDIO_PRAEFIXE.some(praefix => key.startsWith(praefix))) {
        zuLoeschen.push(key)
      }
    }
    // Erst sammeln, dann löschen: die Indizes verschieben sich, sobald man
    // während des Durchlaufs entfernt, und man überspränge jeden zweiten.
    zuLoeschen.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Kein Speicher, nichts aufzuräumen.
  }
}
