import { Phone, Shield, Users } from 'lucide-react'

import { Switch } from '@/components/ui/Switch'

/**
 * Eine Rechteliste, nach Kategorien in Abschnitte gelegt.
 *
 * Es gab diese Ansicht zweimal und beide Male anders: der Reiter der
 * Standardrechte legte die Rechte in Abschnitte („Chat und Mitglieder",
 * „Sprach- und Videoanrufe", „Moderation") und schaltete sie mit einem
 * Schalter; das Rollen-Formular daneben warf dieselben Rechte in eine flache
 * zweispaltige Liste mit Haken und ignorierte das Feld `category`, aus dem die
 * Abschnitte entstehen. Zwei Ansichten auf dasselbe Vokabular, und nur eine
 * davon zeigte seine Ordnung.
 *
 * Dieses Bauteil ist die eine Ansicht. Es übersetzt bewusst nichts: Titel und
 * Beschreibungen kommen fertig herein. Damit passt es auch auf Rechtekataloge,
 * die ihre Texte anders nachschlagen als die Gruppenrechte — der Panelkatalog
 * in `PermissionEditor.tsx` etwa holt sie mit einem Standardwert aus dem
 * Backend.
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
}

/**
 * Ein Abschnitt sammelt eine oder mehrere Kategorien ein.
 *
 * `symbol` steht als eigenes Feld da und wird **nicht** aus dem Titel
 * abgeleitet. Vorher hing das Schild am übersetzten Text (`titel ===
 * 'Moderation'`) — das war auf Deutsch richtig und auf Englisch nie wahr.
 */
export interface RechteAbschnittDefinition {
  titel: string
  symbol: 'chat' | 'anruf' | 'moderation'
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
   * „an" oder „aus" ist, ohne zu sagen, wovon.
   */
  zeilenBeschriftung: (titel: string) => string
  className?: string
}

const SYMBOLE = {
  chat: Users,
  anruf: Phone,
  moderation: Shield,
} as const

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
        // Rechte weglassen, ohne die Abschnittsliste anfassen zu müssen — der
        // Reiter der Standardrechte lässt `manage_roles` aus.
        if (!zeilen.length) return null
        const Symbol = SYMBOLE[abschnitt.symbol]

        return (
          <div
            key={abschnitt.titel}
            className={i > 0 ? 'border-t border-outline-variant/30 pt-5' : ''}
          >
            <div className="mb-3 flex items-center gap-2">
              <Symbol className="h-4 w-4 text-primary" />
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
