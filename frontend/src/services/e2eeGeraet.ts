/**
 * Die E2EE-Identität dieses Geräts — und es gibt nur diese eine.
 *
 * Vorher hing sie am Konto: ein RSA-Paar, dessen privater Teil mit einem
 * Argon2id-Schlüssel aus einem abgetippten Wiederherstellungsschlüssel verpackt
 * beim Server lag, damit jedes Gerät ihn holen konnte. Das war selbst schon die
 * zweite Reparatur eines Mehrgeräteproblems — davor überschrieb jedes neue
 * Gerät den Schlüssel des vorigen.
 *
 * Mit dem Double Ratchet ist ein geteilter privater Schlüssel nicht bloß
 * unnötig, sondern falsch. Der Ratchet ist eine lineare Kette: wer eine
 * Nachricht entschlüsselt, vernichtet dabei den Schlüssel, der sie geöffnet
 * hat. Zwei Geräte mit demselben privaten Schlüssel laufen darum unweigerlich
 * auseinander und verklemmen die Sitzung.
 *
 * Also gehört der Schlüssel jetzt dem Gerät. Er entsteht hier, er bleibt hier,
 * und veröffentlicht wird nur der öffentliche Teil. Kein Dialog, kein Code,
 * nichts zum Aufschreiben: ein Browser bekommt seine Identität nach dem
 * normalen Login, die Tauri-App und die APK nach dem Kopplungscode, den sie
 * ohnehin zum Anmelden einlösen.
 *
 * Was das kostet, steht bewusst hier: **ein Gerät, das seine IndexedDB
 * verliert, verliert seinen Gesprächsfaden.** Es meldet sich mit einem neuen
 * Schlüssel, die Gegenstellen bauen neue Sitzungen auf, und alles davor bleibt
 * unlesbar. Genau dafür gibt es den Verlaufs-Erstabgleich über die Kopplung.
 *
 * **Zwei Paare, zwei Aufgaben.** Neben dem RSA-OAEP-Paar, gegen das
 * verschlüsselt wird, hält ein Gerät seit 09/2026 ein ECDSA-Paar, mit dem es
 * seine Nutzlasten unterschreibt. Warum ein Gruppenschlüssel das nicht leisten
 * kann, steht im Kopf von `nutzlastSignatur.ts`. Keines der beiden darf die
 * Aufgabe des anderen übernehmen; das Backend prüft beide getrennt.
 *
 * Die Umsetzung liegt in `services/geraet/`: Ablage und Identität
 * (`geraeteAblage`), Verzeichnis und Belegprüfung (`geraeteVerzeichnis`),
 * Vertrauenskette und Warnungen (`geraeteVertrauen`) sowie Freigabe und
 * Entfernen (`geraeteFreigabe`). Diese Datei bleibt die öffentliche Fassade,
 * damit kein Aufrufer einen neuen Pfad lernen muss.
 */

import { leereGeraeteRam } from './geraet/geraeteAblage'
import { leereFreigabeRam } from './geraet/geraeteFreigabe'
import { leereVertraute, setzeEigeneFreigabe } from './geraet/geraeteVertrauen'
import { leereVerzeichnisRam } from './geraet/geraeteVerzeichnis'

export { eigenesGeraet, meinKonto, schreibeGeraetBestandNeu, type EigenesGeraet } from './geraet/geraeteAblage'

export {
  E2eeKeinGeraetError,
  geraeteVon,
  kontoNutztSignaturen,
  pruefeGeraeteBeleg,
  signaturSchluesselVon,
  vergessenGeraete,
  verlangeGeraeteVon,
  verzeichnisVon,
  type GeraeteBeleg,
} from './geraet/geraeteVerzeichnis'

export {
  eigenesGeraetFreigegeben,
  entfernenDaten,
  freigabeBelegt,
  freigabeDaten,
  onEigeneFreigabe,
  onSchluesselWarnung,
  sicherheitsnummer,
  vertrauteGeraete,
  type SchluesselWarnungEvent,
  type SchluesselWarnungListener,
  type SchluesselWarnungTyp,
} from './geraet/geraeteVertrauen'

export {
  entferneGeraet,
  gebeGeraetFrei,
  geraetVeroeffentlichen,
  geraeteZuruecksetzen,
  gibMichSelbstFrei,
} from './geraet/geraeteFreigabe'

/**
 * Leert die RAM-Zwischenspeicher. Die IndexedDB überlebt das Abmelden.
 *
 * Der Schlüssel bleibt absichtlich liegen: wer sich wieder anmeldet, soll sein
 * Gespräch vorfinden und nicht jedes Mal eine neue Sitzung aufbauen. Dass er
 * dabei nicht mehr in fremde Hände gerät, regelt der Kontoschlüssel der Ablage,
 * nicht das Aufräumen hier.
 *
 * Alle Teilmodule müssen hier mitspielen: ein Cache, den das Abmelden stehen
 * lässt, gehört dem nächsten Konto.
 */
export function clearGeraeteMemory(): void {
  leereVerzeichnisRam()
  leereVertraute()
  setzeEigeneFreigabe(null)
  leereGeraeteRam()
  leereFreigabeRam()
}
