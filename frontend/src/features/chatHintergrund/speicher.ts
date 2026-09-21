import { HINTERGRUND_VORLAGEN, STANDARD_VORLAGE, type HintergrundId } from './vorlagen'

/**
 * Wo ein Hintergrund steht. Ein Bereich ist eine **Unterhaltungsfläche**, nicht
 * eine Seite: der Messenger mit all seinen Chats ist einer, der KI-Bereich mit
 * Chat-, Guardian-, Worker- und Aufgabenfenster der andere.
 *
 * Zwei Bereiche und nicht einer, weil beides verschiedene Arbeit ist: wer mit
 * Menschen schreibt, will vielleicht ein Foto sehen, wer Logzeilen einer
 * Maschine liest, eher ein ruhiges Raster. Geteilt wird die Mechanik, nicht die
 * Wahl — dasselbe Fenster, dieselben Vorlagen, derselbe Regler, zwei Antworten.
 */
export type ChatHintergrundBereich = 'messenger' | 'ki'

/**
 * Die gespeicherte Form.
 *
 * Englische Feldnamen, obwohl das Modul deutsch heisst: das hier liegt seit
 * Monaten so in den Browsern der Benutzer. Ein Umbenennen wäre eine Migration,
 * deren einziger Gewinn ein hübscherer Schlüsselname im Entwicklerwerkzeug
 * wäre — und deren Preis ein verlorener Hintergrund für jeden, bei dem sie
 * schiefgeht.
 */
export interface ChatHintergrundKonfiguration {
  preset: HintergrundId
  /** Das eigene Bild als Data-URL; nur bei `preset: 'custom'` gesetzt. */
  customDataUrl?: string
  /** 0–75 Prozent Abdunkelung, damit Text auf jedem Bild lesbar bleibt. */
  dimLevel: number
}

export const STANDARD_HINTERGRUND: ChatHintergrundKonfiguration = {
  preset: STANDARD_VORLAGE,
  dimLevel: 25,
}

/**
 * Der Speicherschlüssel je Bereich.
 *
 * **Niemals mit `msm_chat_` beginnen.** `scrubPlaintextStorage()` in
 * `services/e2eeCrypto.ts` löscht jeden Schlüssel mit diesem Präfix, und der
 * Messenger ruft das bei jedem Öffnen auf (`Messenger.tsx`). Genau dort lag
 * der alte Schlüssel `msm_chat_wallpaper_config`: der gewählte Hintergrund
 * überlebte die eigene Sitzung, aber kein zweites Öffnen des Messengers. Die
 * Putzkolonne ist im Recht — sie räumt Klartextreste der Ende-zu-Ende-
 * Verschlüsselung weg —, nur gehört eine Hintergrundwahl nicht in deren
 * Namensraum.
 */
const SCHLUESSEL: Record<ChatHintergrundBereich, string> = {
  messenger: 'msm_hintergrund:messenger',
  ki: 'msm_hintergrund:ki',
}

/** Der Schlüssel von vor dem Umbau; wird einmalig übernommen, siehe unten. */
const ALTER_MESSENGER_SCHLUESSEL = 'msm_chat_wallpaper_config'

export function hintergrundSchluessel(bereich: ChatHintergrundBereich): string {
  return SCHLUESSEL[bereich]
}

/**
 * Das Ereignis, mit dem eine Änderung durchs Fenster läuft.
 *
 * Ohne es zeigte das Guardian-Fenster noch den alten Hintergrund, während der
 * Chat daneben schon den neuen trägt: beide lesen denselben Schlüssel, aber
 * `localStorage` sagt von sich aus nichts (das `storage`-Ereignis des Browsers
 * feuert nur in **anderen** Tabs, nie im eigenen).
 */
export const CHAT_HINTERGRUND_EVENT = 'msm:chat-hintergrund'

const GUELTIGE_IDS: readonly HintergrundId[] = [
  ...HINTERGRUND_VORLAGEN.map((vorlage) => vorlage.id),
  'custom',
]

/** Nachsichtig lesen: alles Unbekannte fällt auf den Standard zurück. */
function ausRohdaten(roh: string | null): ChatHintergrundKonfiguration {
  if (!roh) return STANDARD_HINTERGRUND
  try {
    const gelesen = JSON.parse(roh) as Partial<ChatHintergrundKonfiguration>
    const preset = GUELTIGE_IDS.includes(gelesen.preset as HintergrundId)
      ? (gelesen.preset as HintergrundId)
      : STANDARD_VORLAGE
    // Ein „eigenes Bild" ohne Bild ist kein Hintergrund, sondern ein Loch.
    const customDataUrl =
      typeof gelesen.customDataUrl === 'string' ? gelesen.customDataUrl : undefined
    return {
      preset: preset === 'custom' && !customDataUrl ? STANDARD_VORLAGE : preset,
      customDataUrl,
      dimLevel:
        typeof gelesen.dimLevel === 'number' && Number.isFinite(gelesen.dimLevel)
          ? Math.min(100, Math.max(0, gelesen.dimLevel))
          : STANDARD_HINTERGRUND.dimLevel,
    }
  } catch {
    return STANDARD_HINTERGRUND
  }
}

/**
 * Den alten Messenger-Schlüssel einmalig übernehmen.
 *
 * Wer seinen Hintergrund gewählt und den Messenger seither nicht mehr geöffnet
 * hat, hat ihn noch; alle anderen hat die Putzkolonne längst darum gebracht.
 * Danach ist die alte Zeile weg — zwei Fassungen derselben Wahl wären eine zu
 * viel, und aufgeräumt hätte sie ohnehin der nächste Messenger-Start.
 */
function uebernimmAltbestand(): void {
  try {
    const alt = localStorage.getItem(ALTER_MESSENGER_SCHLUESSEL)
    if (alt === null) return
    if (localStorage.getItem(SCHLUESSEL.messenger) === null) {
      localStorage.setItem(SCHLUESSEL.messenger, alt)
    }
    localStorage.removeItem(ALTER_MESSENGER_SCHLUESSEL)
  } catch {
    /* Ohne Ablage gibt es auch keinen Altbestand. */
  }
}

export function ladeChatHintergrund(
  bereich: ChatHintergrundBereich,
): ChatHintergrundKonfiguration {
  try {
    if (bereich === 'messenger') uebernimmAltbestand()
    return ausRohdaten(localStorage.getItem(SCHLUESSEL[bereich]))
  } catch {
    // Privater Modus, gesperrte Speicherung: der Standard steht trotzdem.
    return STANDARD_HINTERGRUND
  }
}

/**
 * Speichern und alle Mitleser wecken.
 *
 * Gibt `false` zurück, wenn der Browser die Ablage verweigert hat — fast immer
 * ein zu grosses eigenes Bild. Das Fenster sagt das dann; vorher verschwand ein
 * 8-MB-Bild wortlos und kam nach dem nächsten Neuladen nicht wieder.
 */
export function speichereChatHintergrund(
  bereich: ChatHintergrundBereich,
  konfiguration: ChatHintergrundKonfiguration,
): boolean {
  let gespeichert = true
  try {
    localStorage.setItem(SCHLUESSEL[bereich], JSON.stringify(konfiguration))
  } catch {
    gespeichert = false
  }
  // Auch bei verweigerter Ablage: für diese Sitzung gilt die Wahl, und der
  // Chat soll sie sofort zeigen. Verloren ist sie erst beim Neuladen.
  melde(bereich)
  return gespeichert
}

function melde(bereich: ChatHintergrundBereich): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(CHAT_HINTERGRUND_EVENT, { detail: { bereich } }))
}

/**
 * Auf Änderungen dieses Bereichs hören — im eigenen Fenster über das Ereignis
 * oben, aus einem zweiten Tab über `storage`. Liefert die Abmeldung zurück.
 */
export function beobachteChatHintergrund(
  bereich: ChatHintergrundBereich,
  beiAenderung: (konfiguration: ChatHintergrundKonfiguration) => void,
): () => void {
  if (typeof window === 'undefined') return () => undefined

  const ausEigenemFenster = (ereignis: Event) => {
    const gemeldet = (ereignis as CustomEvent<{ bereich?: ChatHintergrundBereich }>).detail?.bereich
    if (gemeldet && gemeldet !== bereich) return
    beiAenderung(ladeChatHintergrund(bereich))
  }

  const ausAnderemTab = (ereignis: StorageEvent) => {
    if (ereignis.key && ereignis.key !== SCHLUESSEL[bereich]) return
    beiAenderung(ladeChatHintergrund(bereich))
  }

  window.addEventListener(CHAT_HINTERGRUND_EVENT, ausEigenemFenster)
  window.addEventListener('storage', ausAnderemTab)
  return () => {
    window.removeEventListener(CHAT_HINTERGRUND_EVENT, ausEigenemFenster)
    window.removeEventListener('storage', ausAnderemTab)
  }
}
