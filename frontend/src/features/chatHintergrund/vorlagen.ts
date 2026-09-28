import { Compass, Layers, RotateCcw, Sparkles } from 'lucide-react'
import type { CSSProperties } from 'react'
import type { LucideIcon } from 'lucide-react'

/**
 * Der Katalog der Hintergründe — die **einzige** Stelle, an der eine Vorlage
 * beschrieben steht.
 *
 * Vorher gab es jede Vorlage zweimal: einmal als Vorschaukachel im
 * Einstellungsfenster und einmal als echte Schicht im Chat. Zwei Fassungen
 * desselben Farbverlaufs sind zwei Wahrheiten darüber, wie „Deep Petrol"
 * aussieht — wer eine davon ändert, ändert die Vorschau oder den Chat, nie
 * beides. Darum steht hier `klassen`/`stil` einmal, und sowohl die Kachel als
 * auch die Schicht zeichnen daraus (siehe `HintergrundFlaeche`).
 *
 * Eine neue Vorlage ist damit ein Eintrag in dieser Liste, sonst nichts:
 * Auswahlfenster und Chat kennen sie beide sofort.
 */
export type HintergrundVorlageId = 'cyber' | 'petrol' | 'midnight' | 'minimal'

/** Die Vorlagen plus „eigenes Bild" — alles, was als Hintergrund stehen kann. */
export type HintergrundId = HintergrundVorlageId | 'custom'

export interface HintergrundVorlage {
  id: HintergrundVorlageId
  /**
   * i18n-Schlüssel, keine fertigen Sätze: der Katalog wird beim Laden des
   * Moduls ausgewertet, ein `t()` von dort stünde nach jedem Sprachwechsel
   * in der alten Sprache.
   *
   * Der Namensraum heisst weiterhin `social.wallpaper.*`. Die Texte sind
   * dieselben geblieben; ein Umzug in `chat.hintergrund.*` hätte nur
   * Übersetzungen verschoben und die alten Schlüssel zu Karteileichen
   * gemacht (siehe `locales/retiredKeys.test.ts`).
   */
  nameKey: string
  hinweisKey: string
  /** Das Zeichen auf der Auswahlkachel. */
  icon: LucideIcon
  /**
   * Die Farbe des Zeichens auf der Kachel. Nur gesetzt, wo sie vom
   * Üblichen abweicht — „Schlicht dunkel" trägt bewusst kein Primärgrün,
   * die Kachel soll aussehen wie das, was sie verspricht.
   */
  iconKlassen?: string
  /** Die Fläche selbst, absolut über dem Chatbereich liegend. */
  klassen: string
  /** Was sich nicht als Klasse schreiben lässt — heute nur das Datenraster. */
  stil?: CSSProperties
}

export const HINTERGRUND_VORLAGEN: readonly HintergrundVorlage[] = [
  {
    id: 'cyber',
    nameKey: 'social.wallpaper.cyber',
    hinweisKey: 'social.wallpaper.cyberHint',
    icon: Layers,
    klassen: 'opacity-20',
    stil: {
      backgroundImage: 'radial-gradient(#06b6d4 1.2px, transparent 1.2px)',
      backgroundSize: '16px 16px',
    },
  },
  {
    id: 'petrol',
    nameKey: 'social.wallpaper.petrol',
    hinweisKey: 'social.wallpaper.petrolHint',
    icon: Compass,
    klassen: 'bg-gradient-to-br from-[#06181d] via-[#092228] to-[#040e11]',
  },
  {
    id: 'midnight',
    nameKey: 'social.wallpaper.midnight',
    hinweisKey: 'social.wallpaper.midnightHint',
    icon: Sparkles,
    klassen: 'bg-gradient-to-br from-[#0c1322] via-[#090e1a] to-[#040810]',
  },
  {
    id: 'minimal',
    nameKey: 'social.wallpaper.minimal',
    hinweisKey: 'social.wallpaper.minimalHint',
    icon: RotateCcw,
    iconKlassen: 'bg-surface-container-highest text-on-surface-variant',
    klassen: 'bg-surface-container-lowest',
  },
]

/** Welche Vorlage steht, wenn niemand etwas gewählt hat. */
export const STANDARD_VORLAGE: HintergrundVorlageId = 'cyber'

export function vorlageMitId(id: HintergrundId): HintergrundVorlage | undefined {
  return HINTERGRUND_VORLAGEN.find((vorlage) => vorlage.id === id)
}
