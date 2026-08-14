import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AI_RUHENDE_LAUFZUSTAENDE,
  aiApi,
  attachAiRun,
  streamAiMessage,
  type AiActionProposal,
  type AiAttachment,
  type AiMessage,
  type AiSection,
  type AiStreamEvent,
  type AiToolPlanAufruf,
  type AiToolUse,
} from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { toast } from '@/stores/toastStore'

/** Ein Eintrag im sichtbaren Verlauf — chronologisch, nicht nach Typ sortiert. */
export type Entry =
  | { kind: 'message'; id: string; message: AiMessage }
  // Marke für das Falten des älteren Verlaufs. Ohne sichtbaren Hinweis
  // würde die KI später Dinge "vergessen", ohne dass jemand weiß warum.
  | { kind: 'compacted'; id: string }
  | { kind: 'proposal'; id: string; proposal: AiActionProposal }

// Hier stand ein `{ kind: 'tool' }` als **eigener** Verlaufseintrag, den
// `insertBeforeStreaming` vor die noch schreibende Blase schob. Das stimmte
// genau solange, wie die KI erst alle Werkzeuge rief und danach redete: dann
// gehörte alles davor. Seit sie während der Arbeit spricht, gehört ein
// Werkzeug **zwischen** zwei Absätze derselben Antwort — und damit in die
// Nachricht, nicht daneben. Ein eigener Eintrag könnte diese Stelle nicht
// benennen.

/** Hängt Text an den letzten Textabschnitt an — oder fängt einen neuen an. */
function mitText(abschnitte: AiSection[] | null | undefined, stueck: string): AiSection[] {
  const bisher = abschnitte ?? []
  const letzter = bisher[bisher.length - 1]
  if (letzter?.art === 'text') {
    return [
      ...bisher.slice(0, -1),
      { ...letzter, inhalt: (letzter.inhalt ?? '') + stueck },
    ]
  }
  return [...bisher, { art: 'text', inhalt: stueck }]
}

function mitWerkzeug(
  abschnitte: AiSection[] | null | undefined, werkzeug: AiToolUse,
): AiSection[] {
  return [...(abschnitte ?? []), { art: 'tool', werkzeug }]
}

/**
 * Dasselbe für den Denktext — anhängen, solange gedacht wird.
 *
 * Er lief früher in ein flaches Feld neben den Abschnitten. Damit gab es nur
 * eine mögliche Stelle, ihn zu zeichnen: ganz oben. Die Gedanken der dritten
 * Runde standen dann über dem Text der ersten, der dort seit zwölf Sekunden
 * stand.
 */
function mitDenken(abschnitte: AiSection[] | null | undefined, stueck: string): AiSection[] {
  const bisher = abschnitte ?? []
  const letzter = bisher[bisher.length - 1]
  if (letzter?.art === 'denken') {
    return [
      ...bisher.slice(0, -1),
      { ...letzter, inhalt: (letzter.inhalt ?? '') + stueck },
    ]
  }
  return [...bisher, { art: 'denken', inhalt: stueck }]
}

/**
 * Der Lauf und sein Ereignisstrom — alles, was ein Lauf mit dem Verlauf macht.
 *
 * Der Grund für den eigenen Hook ist derselbe, der bei `denkwahlFuer` und
 * `providerBeimOeffnen` schon steht: **damit es sich ohne gerendertes Bauteil
 * prüfen lässt**. Die dreizehn Ereigniszweige sind ein Zustand für sich —
 * vier veränderliche Werte in einer Closure, ein Verlauf, ein Lauf, ein
 * Abbruchsignal — und haben mit Providerwahl, Anhängen, Drag-and-drop und
 * Bearbeitungsmodus nichts zu tun. Sie lagen nur zufällig in derselben Datei.
 *
 * Bewusst **kein** Store und **kein** Reducer: der Aufrufer bekommt `entries`
 * und `setEntries` und arbeitet darauf weiter (Bearbeiten schneidet, Leeren
 * räumt ab). Eine Zwischenschicht dafür wäre die nächste Abstraktion ohne
 * Nutzen.
 */
export function useAiLauf({ providerId, canAttach, denken, ladeKontext, setAttachments }: {
  providerId: number | null
  canAttach: boolean
  /**
   * Dieselben zwei Felder, die auf der Leitung stehen und in `ai_runs` landen:
   * **ob** nachgedacht wird und **wie tief**. Mehr braucht der Lauf davon nicht.
   */
  denken: { an: boolean; stufe: string | null }
  /** Den Füllstand neu holen — nach `done` und nach dem Falten. */
  ladeKontext: () => Promise<void>
  setAttachments: (rows: AiAttachment[]) => void
}) {
  const { t } = useTranslation()

  const [entries, setEntries] = useState<Entry[]>([])
  const [streaming, setStreaming] = useState(false)
  /**
   * Was gerade läuft — die einzige Auskunft während der längsten Stille des
   * Ablaufs.
   *
   * Bewusst **kein** Abschnitt der Nachricht: der Plan ist flüchtig und keine
   * Tatsache über die Antwort. Stünde er im Verlauf, behauptete er nach einem
   * Fehlschlag für immer eine Arbeit, die nie stattfand. Deshalb hier neben
   * dem Verlauf, und deshalb geleert, sobald irgendetwas anderes passiert.
   */
  const [laufendeWerkzeuge, setLaufendeWerkzeuge] = useState<AiToolPlanAufruf[]>([])
  // Der Lauf, der gerade noch etwas vorhat. Er überlebt diese Komponente —
  // wir merken ihn uns nur, um uns wieder anhängen zu können.
  const [runId, setRunId] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  // `streaming` ist Zustand und damit für die Ereignisschleife zu spät: zwei
  // Anhängeversuche kurz hintereinander sähen beide noch `false`.
  const streamingRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    // StrictMode führt Setup/Cleanup in Entwicklung absichtlich doppelt aus.
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  /** Ändert genau eine Nachricht im Verlauf. */
  const aendere = useCallback((id: string, update: (message: AiMessage) => AiMessage) => {
    setEntries((current) => current.map((entry) => (
      entry.kind === 'message' && entry.id === id
        ? { ...entry, message: update(entry.message) }
        : entry
    )))
  }, [])

  const merkeVorschlag = useCallback((proposal: AiActionProposal) => {
    setEntries((current) => (
      current.some((entry) => entry.kind === 'proposal' && entry.id === proposal.id)
        ? current.map((entry) => (
            entry.kind === 'proposal' && entry.id === proposal.id
              ? { ...entry, proposal }
              : entry
          ))
        : [...current, { kind: 'proposal', id: proposal.id, proposal }]
    ))
  }, [])

  /**
   * Baut den Ereignisverarbeiter eines Laufs.
   *
   * Bewusst **einer** für beide Wege — frisch gesendet und nachträglich
   * angehängt. Zwei Verarbeiter wären zwei Wahrheiten darüber, wie ein Lauf
   * aussieht, und genau daran bricht so etwas später.
   *
   * `optimistischeId` ist die Blase, die beim Senden schon steht, bevor der
   * Server seine eigene ID vergeben hat. Beim Anhängen gibt es sie nicht.
   */
  const machVerarbeiter = useCallback((
    optimistischeId: string | null,
    optimistischeBenutzerId: string | null = null,
  ) => {
    let aktuell: string | null = optimistischeId
    let offeneOptimistische = optimistischeId !== null
    let offeneBenutzerblase = optimistischeBenutzerId
    let gescheitert = false
    // Hier wurden einmal Kennungen für Werkzeugzeilen vergeben, damit eine live
    // gemeldete Zeile und dieselbe Zeile aus einem späteren Abzug als **eine**
    // erkannt wurden — sonst stand nach dem Wiederanhängen jede doppelt da.
    //
    // Die Frage stellt sich nicht mehr. Werkzeuge sind Abschnitte **innerhalb**
    // einer Nachricht, und der Abzug bringt die Abschnittsliste vollständig mit:
    // sie wird gesetzt, nicht angehängt. Eine Dublette kann so nicht entstehen,
    // und es gibt nichts abzugleichen.

    const verarbeite = ({ event: name, data }: AiStreamEvent) => {
      if (!mountedRef.current) return
      // Ganz oben und nicht bei den anderen Zweigen: das Leeren muss auch dann
      // geschehen, wenn `aktuell` noch leer ist und der `tool`-Zweig weiter
      // unten deshalb gar nicht erreicht wird. Sonst bliebe „Ich lese die
      // Logs“ stehen, bis der Benutzer die Seite neu lädt.
      if (name === 'tool_plan') {
        setLaufendeWerkzeuge(data.aufrufe ?? [])
        return
      }
      if (name === 'tool') {
        // **Einen** Aufruf zurücknehmen, nicht die ganze Ansage.
        //
        // Hier stand `setLaufendeWerkzeuge([])`. Bis zu acht Werkzeuge laufen
        // gleichzeitig; sobald das erste fertig war, verloren die übrigen
        // sieben ihre Zeile, und die Oberfläche fiel auf den allgemeinen Satz
        // zurück — während sie noch arbeiteten.
        //
        // Gemeint ist der Eintrag mit derselben `call_id`. Die trägt das
        // `tool`-Ereignis heute **nicht**: der Server baut es in
        // `_anzeigeeintrag` (ai_stream_service.py) ohne sie, nur `tool_plan`
        // führt sie mit. Deshalb wird über den Namen zugeordnet und genau ein
        // passender Eintrag herausgenommen. Für die Anzeige reicht das
        // vollständig — die Wartezeile fasst ohnehin nach Namen zusammen
        // (AiWartezeile), es zählt allein, wie viele Aufrufe eines Namens noch
        // offen sind. Die Grenze: **welcher** von zwei `read_config` fertig
        // ist, lässt sich so nicht sagen. Sichtbar ist das nicht, solange in
        // der Zeile nur der Name steht; kämen dort Server oder Argumente dazu,
        // braucht es die `call_id` auf der Leitung.
        const fertig = data.tool_name
        setLaufendeWerkzeuge((offen) => {
          const treffer = offen.findIndex((aufruf) => aufruf.tool_name === fertig)
          if (treffer < 0) return offen
          return [...offen.slice(0, treffer), ...offen.slice(treffer + 1)]
        })
      } else if (name === 'segment' || name === 'done' || name === 'error') {
        // Hier endet die Runde wirklich: alles Angekündigte ist hinfällig.
        setLaufendeWerkzeuge([])
      }
      if (name === 'snapshot') {
        setRunId(data.run_id)
        // Der Abzug **ersetzt** den Stand, er ergänzt ihn nicht: er ist die
        // vollständige Antwort bis hierher. Alles anzuhängen würde den Text
        // verdoppeln, wenn man sich während des Schreibens wieder anhängt.
        if (data.message_id) {
          const laeuft = !AI_RUHENDE_LAUFZUSTAENDE.includes(data.status)
          const id = data.message_id
          setEntries((current) => {
            const vorhanden = current.some(
              (entry) => entry.kind === 'message' && entry.id === id,
            )
            const gesetzt = (message: AiMessage): AiMessage => ({
              ...message,
              content: data.content,
              // Die Gliederung kommt vollständig aus dem Abzug — sie **ist**
              // der Grund, warum es ihn gibt. Vorher brachte er `tools` als
              // eigene Liste mit, und die Oberfläche musste raten, wo
              // dazwischen der Text stand.
              sections: data.sections,
              reasoning: data.reasoning || null,
              question: data.question,
              status: laeuft ? 'streaming' : 'complete',
            })
            if (vorhanden) {
              return current.map((entry) => (
                entry.kind === 'message' && entry.id === id
                  ? { ...entry, message: gesetzt(entry.message) }
                  : entry
              ))
            }
            return [...current, {
              kind: 'message',
              id,
              message: gesetzt({
                id, role: 'assistant', content: '', reasoning: null, question: null,
                status: 'streaming', provider_id: providerId, model: null,
                created_at: new Date().toISOString(),
              }),
            }]
          })
          aktuell = id
          offeneOptimistische = false
        }
        data.proposals.forEach(merkeVorschlag)
        return
      }
      if (name === 'run') {
        const ruht = AI_RUHENDE_LAUFZUSTAENDE.includes(data.status)
        setRunId(ruht ? null : data.run_id)
        if (ruht) {
          setStreaming(false)
          setLaufendeWerkzeuge([])
        }
        return
      }
      if (name === 'segment') {
        // Eine Fortsetzung schreibt eine **neue** Nachricht. Die nächste
        // `message` legt sie an; hier wird nur die alte losgelassen.
        aktuell = null
        return
      }
      if (name === 'message') {
        // Die Benutzerblase steht optimistisch mit einer erfundenen ID da.
        // Sie hier zu berichtigen ist keine Kosmetik: die Anhänge dieser Frage
        // sind serverseitig an die **echte** Nachricht gebunden und fanden ihre
        // Blase sonst nie.
        if (offeneBenutzerblase && data.user_message_id) {
          const echteId = data.user_message_id
          const alteId = offeneBenutzerblase
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.id === alteId
              ? { ...entry, id: echteId, message: { ...entry.message, id: echteId } }
              : entry
          )))
          offeneBenutzerblase = null
          // Jetzt tragen die Anhänge eine Nachricht — nachladen, damit sie aus
          // der Chipleiste in ihre Blase wandern.
          if (canAttach) {
            void aiApi.listAttachments()
              .then((rows) => { if (mountedRef.current) setAttachments(rows) })
              .catch(() => undefined)
          }
        }
        if (offeneOptimistische && optimistischeId) {
          // Ab hier kennt der Server die Nachricht unter seiner eigenen ID.
          const neueId = data.message_id
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.id === optimistischeId
              ? { ...entry, id: neueId, message: { ...entry.message, id: neueId } }
              : entry
          )))
          offeneOptimistische = false
        } else {
          const id = data.message_id
          setEntries((current) => (
            current.some((entry) => entry.kind === 'message' && entry.id === id)
              ? current
              : [...current, {
                  kind: 'message',
                  id,
                  message: {
                    id, role: 'assistant', content: '', reasoning: null, question: null,
                    status: 'streaming', provider_id: providerId, model: null,
                    created_at: new Date().toISOString(),
                  },
                }]
          ))
        }
        aktuell = data.message_id
        return
      }
      if (!aktuell && (name === 'delta' || name === 'reasoning' || name === 'question' || name === 'done' || name === 'tool')) {
        return
      }
      if (name === 'delta') {
        // `content` und `sections` gehen zusammen weiter, weil sie
        // Verschiedenes sind: der reine Text und seine Gliederung. Die
        // Gliederung erbt hier ihre eigentliche Aufgabe — ein Werkzeug, das
        // zwischen zwei Absätzen lief, trennt sie in zwei Abschnitte.
        aendere(aktuell!, (message) => ({
          ...message,
          content: message.content + data.content,
          sections: mitText(message.sections, data.content),
        }))
      } else if (name === 'reasoning') {
        // In die Gliederung, an ihre Stelle — nicht in ein flaches Feld
        // daneben. `message.reasoning` bleibt dabei leer und wird erst durch
        // einen Abzug gesetzt; gezeichnet wird ohnehin aus den Abschnitten,
        // und zwei mitgeführte Fassungen desselben Textes liefen früher oder
        // später auseinander.
        aendere(aktuell!, (message) => ({
          ...message, sections: mitDenken(message.sections, data.content),
        }))
      } else if (name === 'question') {
        // Die Frage gehört an die Antwort, nicht neben sie. Als eigener
        // Eintrag stand sie früher VOR der noch leeren Assistentenblase,
        // unter der dann "Keine Antwort erhalten" erschien.
        aendere(aktuell!, (message) => ({ ...message, question: data }))
      } else if (name === 'done') {
        aendere(aktuell!, (message) => ({ ...message, status: 'complete' }))
        // Der Zug ist durch: Frage, Antwort und alles Gelesene stehen jetzt im
        // Kontext. Genau hier hat sich der Füllstand geändert.
        void ladeKontext()
      } else if (name === 'tool') {
        // In die laufende Nachricht, an ihr Ende — dorthin, wo der Aufruf
        // tatsächlich stattgefunden hat. Eine eigene Kennung braucht es dafür
        // nicht mehr: die Stelle in der Liste **ist** die Identität, und ein
        // späterer Abzug bringt dieselbe Liste mit. Genau daran krankte die
        // alte Lösung — sie musste Nummern vergeben, die im Abzug wieder
        // auftauchen konnten.
        aendere(aktuell!, (message) => ({
          ...message, sections: mitWerkzeug(message.sections, data),
        }))
      } else if (name === 'compacted') {
        // Die Marke gehört an den Anfang: sie beschreibt, was *vorher* war.
        setEntries((current) => [
          { kind: 'compacted', id: `compacted-${data.conversation_id}` },
          ...current.filter((entry) => entry.kind !== 'compacted'),
        ])
        // Nach dem Falten ist der Ring die halbe Erklärung für die Zeile
        // darüber: er fällt sichtbar zurück.
        void ladeKontext()
      } else if (name === 'proposal' || name === 'action') {
        merkeVorschlag(data)
      } else if (name === 'error') {
        gescheitert = true
        // Der stabile Code sagt konkret, was fehlt (falscher Key, falsches
        // Modell, falsche Basis-URL). Der allgemeine `message_key` bleibt
        // nur der Rückfall für Codes ohne eigenen Text.
        toast.error(t(`ai.errors.codes.${data.code}`, {
          defaultValue: t(data.message_key, { defaultValue: t('ai.chat.errors.stream') }),
        }))
      }
    }
    return { verarbeite, istGescheitert: () => gescheitert }
  }, [aendere, canAttach, ladeKontext, merkeVorschlag, providerId, setAttachments, t])

  /**
   * Verfolgt einen Lauf, bis er ruht — oder bis der Benutzer weggeht.
   *
   * Geht er weg, bricht **nur die Anzeige** ab. Der Lauf arbeitet auf dem
   * Server weiter; genau das war vorher nicht so.
   */
  const verfolge = useCallback(async (
    beginne: (verarbeite: (event: AiStreamEvent) => void, signal: AbortSignal) => Promise<void>,
    optimistischeId: string | null,
    optimistischeBenutzerId: string | null = null,
  ) => {
    const controller = new AbortController()
    abortRef.current = controller
    const { verarbeite, istGescheitert } = machVerarbeiter(optimistischeId, optimistischeBenutzerId)
    let abgebrochen = false
    try {
      await beginne(verarbeite, controller.signal)
    } catch (error: unknown) {
      if (controller.signal.aborted) {
        abgebrochen = true
      } else {
        toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.stream'))
        setEntries((current) => current.map((entry) => (
          entry.kind === 'message' && entry.message.status === 'streaming'
            ? { ...entry, message: { ...entry.message, status: 'failed' } }
            : entry
        )))
      }
    } finally {
      abortRef.current = null
      if (mountedRef.current && !abgebrochen) {
        setStreaming(false)
        // Der Rückhalt für den Strom, der einfach abreißt: dann kommt kein
        // `error` und kein ruhendes `run`, und die Ankündigung bliebe stehen.
        setLaufendeWerkzeuge([])
        if (istGescheitert()) {
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.message.status === 'streaming'
              ? { ...entry, message: { ...entry.message, status: 'failed' } }
              : entry
          )))
        }
      }
    }
  }, [machVerarbeiter, t])

  /** Hängt sich an einen Lauf, der schon arbeitet. */
  const haengeAn = useCallback(async (id: string) => {
    if (streamingRef.current) return
    setStreaming(true)
    streamingRef.current = true
    try {
      await verfolge(
        (verarbeite, signal) => attachAiRun(id, verarbeite, signal),
        null,
      )
    } finally {
      streamingRef.current = false
    }
  }, [verfolge])

  /**
   * Eine Frage abschicken.
   *
   * `useCallback`, weil die Rückfragekarte in der memoisierten Antwortblase
   * daran hängt: ein frisch gebauter Pfeil je Render machte den Vergleich dort
   * wertlos, und der ganze Verlauf zeichnete sich bei jedem Textstück neu.
   */
  const sendContent = useCallback(async (content: string) => {
    if (!content || !providerId || streaming) return

    const now = new Date().toISOString()
    const assistantId = crypto.randomUUID()
    const optimisticUser: AiMessage = {
      id: crypto.randomUUID(), role: 'user', content, reasoning: null, question: null,
      status: 'complete', provider_id: null, model: null, created_at: now,
    }
    const optimisticAssistant: AiMessage = {
      id: assistantId, role: 'assistant', content: '', reasoning: null, question: null,
      status: 'streaming', provider_id: providerId, model: null, created_at: now,
    }
    setEntries((current) => [
      ...current,
      { kind: 'message', id: optimisticUser.id, message: optimisticUser },
      { kind: 'message', id: assistantId, message: optimisticAssistant },
    ])
    setStreaming(true)
    streamingRef.current = true
    try {
      await verfolge(
        (verarbeite, signal) => streamAiMessage({
          content,
          provider_id: providerId,
          request_id: crypto.randomUUID(),
          reasoning: denken.an,
          reasoning_effort: denken.stufe,
        }, verarbeite, signal),
        assistantId,
        optimisticUser.id,
      )
    } finally {
      streamingRef.current = false
    }
  }, [denken.an, denken.stufe, providerId, streaming, verfolge])

  return {
    entries, setEntries, streaming, laufendeWerkzeuge, runId, setRunId,
    merkeVorschlag, sendContent, haengeAn,
  }
}
