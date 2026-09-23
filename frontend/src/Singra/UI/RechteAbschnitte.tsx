import type { LucideIcon } from 'lucide-react'

import { Switch } from '@/components/ui/Switch'

/**
 * Eine Rechteliste, nach Kategorien in Abschnitte gelegt.
 *
 * **Das eine Aussehen für alles, wo Rechte an einer Rolle hängen.** Es gab
 * diese Ansicht dreimal und jedes Mal anders: der Reiter der Standardrechte im
 * Messenger legte die Rechte in Abschnitte und schaltete sie mit einem
 * Schalter; das Rollen-Formular daneben warf dieselben Rechte in eine flache
 * zweispaltige Liste mit Haken; und der Panel-Rechteeditor zeigte
 * dreispaltige Kacheln mit Titel und roher Kennung, deren Beschreibung man
 * erst zu sehen bekam, wenn man mit der Maus darüberfuhr.
 *
 * Betreiberentscheidung vom 22.09.2026: *„Rollen erstellen sind Rollen
 * erstellen, deshalb beide exakt gleich aussehen."* Das Messenger-Aussehen ist
 * die Vorlage, weil die Beschreibung dort **in der Zeile steht**. Genau
 * deshalb konnte im Panel das eigene Erklärfeld unter der Liste ersatzlos
 * entfallen: es existierte nur, weil in der Kachel kein Platz dafür war.
 *
 * Das Bauteil übersetzt bewusst nichts — Titel und Beschreibungen kommen
 * fertig herein. Der Messenger schlägt sie unter `social.groupRoles.perm.*`
 * nach, der Panelkatalog unter `permissionDetails.*` mit einem Standardwert
 * aus dem Backend. Zwei Wege, ein Aussehen.
 *
 * Die Reihenfolge innerhalb eines Abschnitts ist die der übergebenen Liste. Es
 * gibt bewusst keine zweite Sortierung: die wäre wieder eine Stelle, die man
 * beim nächsten neuen Recht vergisst.
 */

/** Ein Recht, so wie es in der Liste steht — mit fertigem Text. */
export interface RechteZeile {
  /** Der Rechtename, wie ihn das Backend kennt. Geht an `onToggle` zurück. */
  key: string
  /** Bestimmt, in welchem Abschnitt die Zeile landet. */
  kategorie: string
  titel: string
  beschreibung: string
  /**
   * Die rohe Rechte-Kennung, einzeilig in Mono unter der Beschreibung.
   *
   * Nur für den Panelkatalog: dort heißt ein Recht `server.files.read`, und
   * genau diesen Namen tragen Fehlermeldungen, Prüfprotokoll und die
   * Hoster-API. Ein Betreiber, der einem Bericht nachgeht, braucht ihn
   * sichtbar. Die Gruppenrechte im Messenger lassen ihn weg — dort ist die
   * Kennung eine Implementierungssache, die niemandem nützt.
   */
  kennung?: string
}

/**
 * Ein Abschnitt sammelt eine oder mehrere Kategorien ein.
 *
 * `symbol` ist die Icon-Komponente selbst und wird **nicht** aus dem Titel
 * abgeleitet. Vorher hing das Schild am übersetzten Text (`titel ===
 * 'Moderation'`) — das war auf Deutsch richtig und auf Englisch nie wahr.
 * Und es ist bewusst keine feste Auswahl an Namen: der Panelkatalog hat acht
 * Gruppen, der Messenger drei, und eine Liste im Bauteil wäre eine Kopplung
 * an fremdes Vokabular.
 */
export interface RechteAbschnittDefinition {
  titel: string
  symbol: LucideIcon
  kategorien: readonly string[]
}

export interface RechteAbschnitteProps {
  rechte: readonly RechteZeile[]
  abschnitte: readonly RechteAbschnittDefinition[]
  /** Die Rechte, die gerade gesetzt sind. */
  gesetzt: ReadonlySet<string>
  onToggle: (key: string, an: boolean) => void
  disabled?: boolean
  /**
   * Baut die Vorlesebeschriftung des Schalters aus dem Titel des Rechts.
   *
   * Pflicht, weil ein Schalter ohne Beschriftung für eine Sprachausgabe nur
   * „an" oder „aus" ist, ohne zu sagen, wovon. Bei rund 90 Rechten
   * hintereinander ist das der Unterschied zwischen benutzbar und nicht.
   */
  zeilenBeschriftung: (titel: string) => string
  className?: string
}

export function RechteAbschnitte({
  rechte,
  abschnitte,
  gesetzt,
  onToggle,
  disabled = false,
  zeilenBeschriftung,
  className = '',
}: RechteAbschnitteProps) {
  return (
    <div
      className={`rounded-2xl border border-outline-variant/30 p-4 sm:p-6 bg-surface-container/60 shadow-sm space-y-5 ${className}`}
    >
      {abschnitte.map((abschnitt, i) => {
        const zeilen = rechte.filter((r) => abschnitt.kategorien.includes(r.kategorie))
        // Ein Abschnitt ohne Rechte wird nicht gezeichnet. So kann der Aufrufer
        // Rechte weglassen oder filtern, ohne die Abschnittsliste anzufassen —
        // der Reiter der Standardrechte lässt `manage_roles` aus, die Suche im
        // Panel lässt alles aus, was nicht passt.
        if (!zeilen.length) return null
        const Symbol = abschnitt.symbol

        return (
          <div
            key={abschnitt.titel}
            className={i > 0 ? 'border-t border-outline-variant/30 pt-5' : ''}
          >
            <div className="mb-3 flex items-center gap-2">
              <Symbol className="h-4 w-4 text-primary shrink-0" />
              <span className="text-body-sm font-bold text-primary">{abschnitt.titel}</span>
            </div>
            <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2">
              {zeilen.map((zeile) => (
                <div
                  key={zeile.key}
                  className="flex items-center justify-between gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-high/60 p-3.5"
                >
                  <div className="min-w-0 flex-1">
                    <span className="block text-xs font-bold text-primary">{zeile.titel}</span>
                    <span className="text-label-sm leading-snug text-on-surface-variant">
                      {zeile.beschreibung}
                    </span>
                    {zeile.kennung && (
                      <span className="mt-1 block truncate font-mono text-label-sm text-on-surface-variant/70">
                        {zeile.kennung}
                      </span>
                    )}
                  </div>
                  <Switch
                    checked={gesetzt.has(zeile.key)}
                    onCheckedChange={(an) => onToggle(zeile.key, an)}
                    disabled={disabled}
                    aria-label={zeilenBeschriftung(zeile.titel)}
                  />
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
