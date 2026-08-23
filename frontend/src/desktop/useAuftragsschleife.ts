/**
 * Die Schleife, die den Rechner mitarbeiten lässt.
 *
 * Einmal pro Sekunde fragen, ob etwas ansteht, es an Rust weiterreichen, das
 * Ergebnis melden. Fragen statt gehalten werden: eine offene Verbindung je
 * Rechner müsste jeden Abbruch selbst bemerken und wäre bei mehreren
 * Arbeitsprozessen im Panel im falschen Prozess. Eine Sekunde Verzug fällt
 * neben den gut drei Sekunden nicht auf, die ein Anbieter allein bis zum
 * ersten Byte braucht.
 *
 * Läuft nur, solange jemand angemeldet ist — vorher gibt es kein Token, und
 * jede Frage wäre ein 401.
 */
import { useEffect, useRef, useState } from 'react'

import { auftragAusfuehren } from './tauri'
import { ergebnisMelden, naechsterAuftrag, type Auftrag } from './desktopJobs'

const TAKT_MS = 1000
/** Nach so vielen Fehlschlägen am Stück wird der Takt langsamer. */
const RUHE_NACH = 3
const RUHE_MS = 15_000

/**
 * Wartet dieser Auftrag auf eine Entscheidung des Menschen am Rechner?
 *
 * Zwei tun das: die Bitte um Maus und Tastatur und — bei ausgeschaltetem
 * autonomem Modus — das Aufräumen. Ob Letzteres wirklich fragt, weiß hier
 * niemand: das entscheidet Rust anhand des `autonom`, das allein das Panel
 * setzt. Deshalb wird die Kennung schon **vor** dem Ausführen gemerkt und
 * hinterher wieder verworfen, wenn doch ein Ergebnis kam.
 *
 * Seit die Kennung mit in den Aufruf geht, ist dieser gemerkte Wert nur noch
 * der Rückfall: die Karte liest sie aus ihrem eigenen Ereignis.
 */
function fragtEinenMenschen(auftrag: Auftrag): boolean {
  if (auftrag.tool_name === 'desktop_aufraeumen') {
    return true
  }
  return (
    auftrag.tool_name === 'desktop_steuern' &&
    (auftrag.arguments as { aktion?: unknown } | null)?.aktion === 'freigabe'
  )
}

/**
 * Startet die Schleife und gibt die Kennung des Auftrags zurück, der gerade
 * auf einen Menschen wartet — oder `null`. Genau diese Aufträge beantwortet
 * nicht die Schleife, sondern die jeweilige Karte.
 */
export function useAuftragsschleife(aktiv: boolean): string | null {
  const [offeneUebernahme, setOffeneUebernahme] = useState<string | null>(null)
  // In einem Ref und nicht im State: die Schleife soll sich davon nicht neu
  // aufbauen, und niemand rendert deswegen.
  const laeuft = useRef(false)

  useEffect(() => {
    if (!aktiv) {
      return
    }
    let gestoppt = false
    let fehlschlaege = 0

    async function abarbeiten(auftrag: Auftrag) {
      let ergebnis: Record<string, unknown> | null
      try {
        // Die Kennung **vor** dem Ausführen merken: Rust schickt das Ereignis
        // für die Karte noch im Aufruf los, und eine Karte ohne Auftrag könnte
        // nichts beantworten. Dieser Weg ist aber nur der Rückfall — er hängt
        // daran, dass das `set` samt Neu-Render vor dem Ereignis durchläuft,
        // und niemand sichert das zu.
        if (fragtEinenMenschen(auftrag)) {
          setOffeneUebernahme(auftrag.id)
        }
        // Der verlässliche Weg ist dieser: die Kennung reist im Aufruf mit,
        // Rust legt sie in die Nutzlast der Karte, und die Karte beantwortet
        // damit genau **diesen** Auftrag — auch wenn oben noch eine ältere
        // Kennung steht oder das Ereignis schneller war als React.
        ergebnis = await auftragAusfuehren(auftrag.tool_name, auftrag.arguments, auftrag.id)
      } catch (fehler) {
        setOffeneUebernahme(null)
        // Der Fehlertext geht als Werkzeugergebnis an das Modell — er nennt
        // den Grund, damit es nicht dasselbe dreimal versucht. Pfade darin
        // sind die des Benutzers und bleiben auf seinem Rechner: gemeldet
        // wird nur, was Rust selbst formuliert hat.
        const text = fehler instanceof Error ? fehler.message : String(fehler)
        await ergebnisMelden(auftrag.id, false, { fehler: text }, 'DESKTOP_TOOL_FAILED')
        return
      }
      // `null` heißt: das Ergebnis kommt später, weil ein Mensch entscheidet.
      // Die jeweilige Karte meldet dann selbst (Uebernahmekarte.tsx,
      // Aufraeumkarte.tsx). Beim Aufräumen im autonomen Modus kommt statt
      // `null` ein fertiges Ergebnis — dann läuft es hier normal weiter.
      if (ergebnis === null) {
        return
      }
      setOffeneUebernahme(null)

      // **Melden ist nicht Ausführen.** Beides stand einmal in demselben
      // try-Block, und ein Netzfehler beim Melden landete deshalb im
      // Fehlerzweig darüber: der meldete denselben Auftrag ein zweites Mal —
      // als gescheitertes Werkzeug, mit dem Transportfehler als Grund. Das
      // fertige Ergebnis war damit weg, obwohl der Rechner es hatte.
      //
      // Stattdessen zweimal nachfassen. Das Ergebnis liegt noch hier, der
      // Auftrag ist panelseitig offen, und die Frist (180 s) lässt Luft.
      // Bleibt es dabei, wird nichts erfunden: der Auftrag verfällt, und das
      // Modell erfährt genau das.
      for (let versuch = 0; versuch < 3; versuch += 1) {
        try {
          await ergebnisMelden(auftrag.id, true, ergebnis)
          return
        } catch {
          if (versuch === 2) {
            return
          }
          await new Promise((weiter) => setTimeout(weiter, 800 * (versuch + 1)))
        }
      }
    }

    async function schleife() {
      if (laeuft.current) {
        return
      }
      laeuft.current = true
      try {
        while (!gestoppt) {
          try {
            const auftrag = await naechsterAuftrag()
            fehlschlaege = 0
            if (auftrag) {
              await abarbeiten(auftrag)
              // Sofort weiterfragen: eine Runde besteht oft aus mehreren
              // Aufträgen, und der Lauf wartet auf den letzten davon.
              continue
            }
          } catch {
            // Panel nicht erreichbar, Sitzung abgelaufen, Netz weg. Kein
            // Grund aufzugeben — nur ein Grund, seltener zu fragen.
            fehlschlaege += 1
          }
          const pause = fehlschlaege >= RUHE_NACH ? RUHE_MS : TAKT_MS
          await new Promise((weiter) => setTimeout(weiter, pause))
        }
      } finally {
        laeuft.current = false
      }
    }

    void schleife()
    return () => {
      gestoppt = true
    }
  }, [aktiv])

  return offeneUebernahme
}
