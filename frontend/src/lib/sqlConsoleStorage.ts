/**
 * Speicher der SQL-Konsole: Abfrageverlauf und Favoriten.
 *
 * Warum eigene Schlüssel statt fester Namen? localStorage gehört der Herkunft
 * und nicht der Anmeldung — er überlebt das Abmelden und kennt keinen Benutzer.
 * Ein fester Name wie 'msm_sql_history' zeigt darum dem nächsten Benutzer, der
 * sich am selben Rechner anmeldet, die Abfragen des vorigen; im Abfragetext
 * stehen Tabellen, Spalten und die Literale aus UPDATE- und INSERT-Anweisungen.
 * Deshalb trägt jeder Schlüssel die Benutzerkennung und den Namen der Konsole
 * (Paneldatenbank oder ein bestimmter Server) — ohne den zweiten Teil wandert
 * der Verlauf aus der Datenbank von Server 1 in die Konsole von Server 2, wo
 * der Benutzer vielleicht nur lesen darf.
 *
 * Die Datei importiert absichtlich keinen Store: authStore ruft hier auf, ein
 * Rückimport wäre ein Importzyklus.
 */
export const SQL_CONSOLE_STORAGE_PREFIX = 'msm_sql'

export interface SqlConsoleStorageKeys {
  history: string
  favorites: string
}

/**
 * Baut die beiden Schlüssel einer Konsole. `scope` benennt die Konsole selbst,
 * z. B. 'panel' oder 'server-7'; die Datenbanken eines Servers teilen sich den
 * Verlauf, weil auch die aufrufende Seite nur einen Sitzungsverlauf je Konsole
 * führt — ein Schlüssel je Datenbank wäre ein Versprechen, das die Komponente
 * nicht halten könnte.
 */
export function sqlConsoleStorageKeys(userId: number | string, scope: string): SqlConsoleStorageKeys {
  return {
    history: `${SQL_CONSOLE_STORAGE_PREFIX}:history:${userId}:${scope}`,
    favorites: `${SQL_CONSOLE_STORAGE_PREFIX}:favorites:${userId}:${scope}`,
  }
}

/**
 * Liest eine Liste. Kaputter oder fremder Inhalt gilt als leer: der Verlauf ist
 * Komfort, er darf die Konsole nicht lahmlegen, wenn jemand im Speicher rührt.
 */
export function readSqlConsoleEntries<T>(key: string): T[] {
  try {
    const saved = localStorage.getItem(key)
    if (!saved) return []
    const parsed = JSON.parse(saved)
    return Array.isArray(parsed) ? (parsed as T[]) : []
  } catch {
    return []
  }
}

export function writeSqlConsoleEntries<T>(key: string, entries: T[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(entries))
  } catch {
    // Speicher voll oder gesperrt (privates Fenster): am Verlauf hängt nichts.
  }
}

/**
 * Räumt beim Abmelden auf.
 *
 * Der Verlauf entsteht nebenbei bei jeder Ausführung und hat nach der Sitzung
 * niemandem mehr zu dienen; er verschwindet deshalb ganz — samt der alten,
 * ungebundenen Schlüssel aus der Zeit vor der Benutzerbindung, die sonst als
 * Altlast im Browser des geteilten Rechners liegen blieben. Favoriten legt der
 * Benutzer bewusst und benannt an; sie bleiben unter seinem Schlüssel liegen
 * und sind für andere Benutzer ohnehin unsichtbar.
 */
export function clearSqlConsoleHistory(): void {
  try {
    const zuLoeschen: string[] = []
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (!key || !key.startsWith(SQL_CONSOLE_STORAGE_PREFIX)) continue
      if (key.startsWith(`${SQL_CONSOLE_STORAGE_PREFIX}:favorites:`)) continue
      zuLoeschen.push(key)
    }
    // Erst sammeln, dann löschen: die Indizes verschieben sich, sobald man
    // während des Durchlaufs entfernt, und man überspränge jeden zweiten.
    zuLoeschen.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Kein Speicher, nichts aufzuräumen.
  }
}
