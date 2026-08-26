import { memo, useEffect, useState } from 'react'
import { AlertTriangle, BookOpen, Bot, BrainCircuit, Calendar, CalendarClock, ChevronDown, ChevronRight, Loader2, Mail, Sparkles, User, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type {
  AiActionProposal,
  AiMessage,
  AiSection,
  AiToolPlanAufruf,
  AiToolUse,
} from '@/api/ai'
import { AiActionProposalCard } from './AiActionProposalCard'
import { AiMarkdown } from './AiMarkdown'
import { AiQuestionCard } from './AiQuestionCard'
import { AiReasoningBlock } from './AiReasoningBlock'
import type { Entry } from './useAiLauf'

/**
 * Wie ein Lauf aussieht — einmal, für beide Fenster.
 *
 * Diese Bauteile standen alle in `AiChat.tsx` und waren dort modulprivat. Das
 * ging, solange es genau ein Fenster gab. Seit die Guardian-Reparatur ihr
 * eigenes hat, gäbe es zwei Möglichkeiten: dieselbe Blase ein zweites Mal
 * schreiben, oder sie hierher holen. Zwei Zeichner desselben Verlaufs wären
 * zwei Wahrheiten darüber, wie ein Zug der KI aussieht — und sie liefen
 * auseinander, sobald einer von beiden ein neues Feld bekommt.
 *
 * `AiChat` behält seine eigene Schleife: dort hängen Bearbeiten, Anhänge und
 * das Eingabefeld dran, und das gehört nicht in einen Verlauf, den man nur
 * liest. Geteilt wird, was den **Inhalt** zeichnet.
 */

/**
 * Nachrichten und Vorschläge in einer Liste, chronologisch.
 *
 * Vorschläge stehen zwischen den Nachrichten und nicht gesammelt am Ende:
 * sonst sieht man nicht, auf welche Antwort sie sich beziehen.
 */
export function mergeEntries(
  messages: AiMessage[],
  proposals: AiActionProposal[],
): Entry[] {
  const merged: Entry[] = [
    ...messages.map((message) => ({ kind: 'message' as const, id: message.id, message })),
    ...proposals.map((proposal) => ({ kind: 'proposal' as const, id: proposal.id, proposal })),
  ]
  return merged.sort((a, b) => entryTimestamp(a).localeCompare(entryTimestamp(b)))
}

/** Zeitstempel eines Eintrags; typlose Marken sortieren an den Anfang. */
function entryTimestamp(entry: Entry): string {
  if (entry.kind === 'message') return entry.message.created_at
  if (entry.kind === 'proposal') return entry.proposal.created_at
  return ''
}

/**
 * Formatiert den Sendezeitpunkt einer Chatnachricht lesbar und kompakt.
 * - Heute: 14:05
 * - Gestern: Gestern 14:05
 * - Älter (gleiches Jahr): 20.08. 14:05
 * - Anderes Jahr: 20.08.2025 14:05
 */
export function formatMessageTime(dateInput: string | Date | undefined | null): string {
  if (!dateInput) return ''
  const date = typeof dateInput === 'string' ? new Date(dateInput) : dateInput
  if (isNaN(date.getTime())) return ''

  const now = new Date()
  const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  const isToday =
    date.getDate() === now.getDate() &&
    date.getMonth() === now.getMonth() &&
    date.getFullYear() === now.getFullYear()

  if (isToday) {
    return timeStr
  }

  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const isYesterday =
    date.getDate() === yesterday.getDate() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getFullYear() === yesterday.getFullYear()

  if (isYesterday) {
    return `Gestern ${timeStr}`
  }

  const isSameYear = date.getFullYear() === now.getFullYear()
  const day = String(date.getDate()).padStart(2, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')

  if (isSameYear) {
    return `${day}.${month}. ${timeStr}`
  }

  return `${day}.${month}.${date.getFullYear()} ${timeStr}`
}

/** Ein gezeichneter Block: ein Absatz, ein Denkkasten oder eine Werkzeuggruppe. */
type Teil =
  | { art: 'text'; inhalt: string }
  | { art: 'denken'; inhalt: string }
  | { art: 'tools'; werkzeuge: AiToolUse[] }

/**
 * Fasst **aufeinanderfolgende** Werkzeuge zu einer Gruppe zusammen.
 *
 * Der Betreiber wollte die Werkzeuge vergangener Nachrichten sehen, aber
 * eingeklappt. Beides zugleich geht nur, wenn die Gruppierung der Reihenfolge
 * folgt: eine einzige Liste am Ende der Blase waere eingeklappt zwar
 * uebersichtlich, verloere aber genau die Zuordnung, um die es geht — welcher
 * Satz vor welchem Aufruf stand.
 *
 * Text und Denkkästen werden **nicht** gruppiert: sie stehen für sich, und
 * zwei Denkkästen hintereinander kann es ohnehin nicht geben — der Vermittler
 * hängt an den laufenden Abschnitt an.
 */
export function gruppiert(abschnitte: AiSection[]): Teil[] {
  const raus: Teil[] = []
  for (const abschnitt of abschnitte) {
    if (abschnitt.art === 'tool' && abschnitt.werkzeug) {
      const letzte = raus[raus.length - 1]
      if (letzte?.art === 'tools') letzte.werkzeuge.push(abschnitt.werkzeug)
      else raus.push({ art: 'tools', werkzeuge: [abschnitt.werkzeug] })
    } else if (abschnitt.art === 'denken' && abschnitt.inhalt) {
      // Leere Denkabschnitte fallen weg wie leere Textabschnitte: ein Kasten
      // ohne Inhalt behauptet eine Überlegung, die es nicht gab.
      raus.push({ art: 'denken', inhalt: abschnitt.inhalt })
    } else if (abschnitt.art === 'text' && abschnitt.inhalt) {
      raus.push({ art: 'text', inhalt: abschnitt.inhalt })
    }
  }
  return raus
}

/**
 * Die leere Ankündigung — **eine** Liste für alle Blasen, die keine haben.
 *
 * Ein frisches `[]` je Render machte den `memo`-Vergleich von `AiAntwortblase`
 * für den gesamten Verlauf wertlos, ohne dass ein Test es merkt.
 */
export const KEINE_AUFRUFE: AiToolPlanAufruf[] = []

/**
 * Eine Antwort der KI: Gedanken, Text, Werkzeuge und eine mögliche Rückfrage.
 *
 * Ein eigenes Bauteil und `memo` — das ist der ganze Grund, warum es sie gibt.
 * Während eine Antwort einläuft, baut `aendere` bei jedem Textstück ein neues
 * `entries`-Array; die nicht betroffenen Einträge gibt es dabei unverändert
 * zurück, ihre `message` bleibt also referenzgleich. Nur deshalb fällt hier
 * jede Blase außer der schreibenden durch den Standardvergleich. Vorher lief
 * `gruppiert` je Textstück über **alle** Nachrichten des Verlaufs.
 *
 * Damit das so bleibt, müssen alle Eigenschaften stabil sein: `onAnswer` ist
 * beim Aufrufer ein `useCallback`. Ein Pfeil an dieser Stelle machte den
 * Vergleich still wertlos, ohne dass ein Test es merkt.
 */
export const AiAntwortblase = memo(function AiAntwortblase({
  message, beantwortet, busy, laufendeWerkzeuge, onAnswer,
}: {
  message: AiMessage
  beantwortet: boolean
  busy: boolean
  /** Was gerade läuft — leer, wenn diese Blase nicht mehr schreibt. */
  laufendeWerkzeuge: AiToolPlanAufruf[]
  /**
   * Beantwortet eine Rückfrage. `null` heißt: hier antwortet niemand — das
   * Guardian-Fenster hat kein Eingabefeld, und eine Karte mit Knöpfen, die
   * nichts auslösen, wäre schlimmer als keine.
   *
   * Ein unbeaufsichtigter Lauf stellt ohnehin keine Rückfragen: `ask_user`
   * wird dort als Werkzeugergebnis abgewiesen (`ai_stream_service`). Die Karte
   * kann im Guardian-Fenster also nur aus einem Verlauf von früher stammen.
   */
  onAnswer: ((label: string) => void) | null
}) {
  const { t } = useTranslation()
  const isStreaming = message.status === 'streaming'
  // Einmal je Nachricht gruppieren, nicht je Abschnitt: `gruppiert` geht über
  // alle Abschnitte, zweimal aufgerufen wäre daraus quadratische Arbeit.
  const teile = message.sections?.length
    ? gruppiert(message.sections)
    : null
  return (
    <article className="flex gap-3">
      <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
        <Bot className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        {/* Nachrichten aus der Zeit vor den Denkabschnitten: dort gibt es die
            Gedanken nur am Stück, ohne jede Stelle. Dann steht der Kasten wie
            früher oben — besser als gar nicht, und es ist die einzige
            Anordnung, die sich für sie nicht raten lässt. Alles Neuere
            zeichnet unten in `teile` einen Block je Runde. */}
        {message.reasoning && !teile?.some((teil) => teil.art === 'denken') && (
          <AiReasoningBlock content={message.reasoning} streaming={false} />
        )}
        {teile?.length ? (
          // Der Zug in seiner tatsächlichen Reihenfolge: Gedanke, Satz,
          // Werkzeuge, Gedanke, Satz. Genau so ist er entstanden, und genau so
          // hat der Benutzer ihn live gesehen — nach einem Neuladen soll er
          // nicht anders aussehen.
          <div className="space-y-3">
            {teile.map((teil, stelle) => (
              teil.art === 'tools' ? (
                <AiWerkzeuggruppe
                  key={stelle}
                  werkzeuge={teil.werkzeuge}
                  // Während die Antwort entsteht, ist das Aufgeklappte die
                  // Antwort: der Benutzer sieht, dass gearbeitet wird. Ist sie
                  // fertig, ist es ein Beleg, den man nachschlagen kann — und
                  // der den Verlauf nicht zustellen soll.
                  offenVoreingestellt={isStreaming}
                />
              ) : teil.art === 'denken' ? (
                <AiReasoningBlock
                  key={stelle}
                  content={teil.inhalt}
                  // Nur der letzte Block denkt noch. Alle früheren sind
                  // abgeschlossen und klappen zu — sonst pulsten drei Kästen
                  // gleichzeitig und behaupteten dasselbe.
                  streaming={isStreaming && stelle === teile.length - 1}
                />
              ) : (
                <AiMarkdown key={stelle} content={teil.inhalt} />
              )
            ))}
          </div>
        ) : message.content ? (
          <AiMarkdown content={message.content} />
        ) : !isStreaming && !message.question && !message.reasoning ? (
          // Eine Rückfrage *ist* die Antwort. Früher stand hier "Keine Antwort
          // erhalten" unter jeder gestellten Frage, weil die Frage in einer
          // eigenen Karte lag und der Nachrichtentext leer blieb. Ebenso ist ein
          // Denkabschnitt eine Ausgabe, unter der kein fälschliches "Keine Antwort" steht.
          <p className="text-sm text-on-surface-variant">{t('ai.chat.noResponse')}</p>
        ) : null}
        {/* Die Wartezeile steht **einmal** und immer als letzte Zeile der
            Blase — nicht mehr an drei Stellen mit drei Bedingungen. Genau
            daraus entstanden die zwei Anzeigen übereinander und der leere
            Denkkasten, der nur behauptete, dass gedacht wird. Der Denkkasten
            erscheint jetzt allein dann, wenn wirklich Denktext fließt: er ist
            oben ein `teil`, kein Platzhalter. */}
        {isStreaming && <AiWartezeile aufrufe={laufendeWerkzeuge} />}
        {/* Die Rückfrage gehört in dieselbe Blase wie der Text davor — sie ist
            Teil dieser Antwort und keine neue Nachricht. */}
        {message.question && onAnswer && (
          <AiQuestionCard
            question={message.question}
            answered={beantwortet}
            disabled={busy}
            onAnswer={onAnswer}
          />
        )}
        {message.created_at && !isStreaming && (
          <p className="mt-1 text-[10px] text-on-surface-variant/60">
            {formatMessageTime(message.created_at)}
          </p>
        )}
        {message.status === 'failed' && (
          <p className="mt-2 text-xs text-status-error">{t('ai.chat.failed')}</p>
        )}
      </div>
    </article>
  )
})

/** Eine einzelne Werkzeugzeile: Symbol, Bezeichnung, ggf. Fehlschlag. */
function AiWerkzeugzeile({ tool }: { tool: AiToolUse }) {
  const { t } = useTranslation()
  // Die Gruppe kommt aus der Registry mit. Vorher stand hier
  // `tool_name === 'remember'` — `search_memory` und `forget_memory` tragen
  // dieselbe Gruppe und bekamen trotzdem das allgemeine Werkzeugsymbol.
  const gruppe = tool.gruppe
  const skillKey = tool.skill_key
  // Ein Skill bekommt seinen Namen in den Verlauf, nicht den Werkzeugnamen:
  // "Skill *Valheim braucht 6 GB* gelernt" sagt etwas, "learn_skill" nichts.
  const skillLabel = skillKey
    ? t(
        tool.skill_learned
          ? (tool.skill_status === 'pending'
              ? 'ai.skills.learnedPending'
              : 'ai.skills.learned')
          : 'ai.skills.used',
        { name: tool.skill_name || skillKey },
      )
    : null
  return (
    <p className="flex items-center gap-2 text-xs text-on-surface-variant">
      {gruppe === 'skill'
        ? <Sparkles className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />
        : gruppe === 'memory'
          ? <BrainCircuit className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          : gruppe === 'docs'
            ? <BookOpen className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />
            : gruppe === 'tasks'
              ? <CalendarClock className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />
              : gruppe === 'mailbox'
                ? <Mail className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />
                : gruppe === 'calendar'
                  ? <Calendar className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />
                  : <Wrench className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />}
      {skillLabel ?? t(`ai.tools.${tool.tool_name}`, { defaultValue: tool.tool_name })}
      {/* Ohne diesen Zusatz behauptet die Zeile einen Beleg, den es nicht gibt
          — der gefaehrlichste Fall bei den Doku-Werkzeugen. */}
      {tool.failed && (
        <span className="inline-flex items-center gap-1 text-status-error">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('ai.chat.toolFailed')}
        </span>
      )}
    </p>
  )
}

/**
 * Eine Gruppe aufeinanderfolgender Werkzeuge — aufklappbar.
 *
 * Waehrend die Antwort entsteht, steht sie offen: das Zusehen **ist** hier die
 * Rueckmeldung, und ein zugeklappter Kasten waehrend einer Minute Arbeit waere
 * wieder das, was abgeschafft werden sollte. Ist die Antwort fertig, klappt
 * sie zu — dann ist sie ein Beleg zum Nachschlagen, und ein Verlauf aus
 * zwanzig Werkzeugzeilen liest sich niemand durch.
 *
 * Der Zustand haengt am Bauteil und nicht am Verlauf: was jemand aufgeklappt
 * hat, geht beim naechsten Neuladen wieder zu. Das ist die richtige Richtung —
 * eingeklappt ist der ruhige Zustand.
 */
function AiWerkzeuggruppe(
  { werkzeuge, offenVoreingestellt }: {
    werkzeuge: AiToolUse[]
    offenVoreingestellt: boolean
  },
) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(offenVoreingestellt)
  // Waechst die Gruppe waehrend des Schreibens weiter, soll sie offen bleiben —
  // und beim Uebergang auf "fertig" zugehen, ohne eine Entscheidung des
  // Benutzers zu ueberschreiben, die es vorher gar nicht geben konnte.
  useEffect(() => { setOffen(offenVoreingestellt) }, [offenVoreingestellt])

  // Die Zeile "es geht weiter" stand früher hier drin, einmal je Rückgabe.
  // Sie gehört nicht der Gruppe: sie sagt etwas über die **Antwort**, nicht
  // über diese Werkzeuge, und sie stand deshalb bei jeder Änderung an dieser
  // Stelle wieder zur Debatte. Jetzt zeichnet `AiAntwortblase` sie einmal am
  // Ende der Blase — siehe `AiWartezeile`.

  if (offen) {
    return (
      <div className="space-y-1">
        {werkzeuge.map((werkzeug, stelle) => (
          <AiWerkzeugzeile key={stelle} tool={werkzeug} />
        ))}
        {!offenVoreingestellt && (
          <button
            type="button"
            onClick={() => setOffen(false)}
            className="flex items-center gap-1 text-xs text-on-surface-variant hover:text-on-surface"
          >
            <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('ai.chat.toolsCollapse')}
          </button>
        )}
      </div>
    )
  }
  const gescheitert = werkzeuge.some((werkzeug) => werkzeug.failed)
  return (
    <button
      type="button"
      onClick={() => setOffen(true)}
      className="flex items-center gap-2 text-xs text-on-surface-variant hover:text-on-surface"
    >
      <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <Wrench className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />
      {t('ai.chat.toolsUsed', { count: werkzeuge.length })}
      {/* Ein Fehlschlag darf sich nicht hinter dem Zuklappen verstecken: er ist
          genau die Auskunft, wegen der die Zeile ueberhaupt existiert. */}
      {gescheitert && (
        <span className="inline-flex items-center gap-1 text-status-error">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('ai.chat.toolFailed')}
        </span>
      )}
    </button>
  )
}

/**
 * Die eine Wartezeile einer noch schreibenden Antwort.
 *
 * Gemessen ist das die längste Stille des ganzen Ablaufs: zwischen dem letzten
 * Werkzeugchip und dem ersten Zeichen der nächsten Runde vergingen bis zu 17,5
 * Sekunden, in denen die Oberfläche **nichts** Konkretes anzeigte. Daran, wie
 * lange es dauert, ändert die Anzeige nichts — aber sie kann sagen, woran
 * gerade gearbeitet wird, statt nur, dass gearbeitet wird.
 *
 * Ohne Ankündigung bleibt der allgemeine Hinweis: es steht nie nichts da.
 *
 * **Mehrere gleichzeitige Werkzeuge werden zu je einer Zeile** — und nach
 * Werkzeugnamen zusammengefasst. Die Sätze sind vollständige Ich-Sätze
 * ("Ich sehe mir die Aufgabenliste an"); aneinandergereiht ergäben sie kein
 * Deutsch, und zweimal derselbe Satz untereinander sähe aus wie ein Fehler.
 * Der Unterschied zwischen zwei Aufrufen desselben Werkzeugs liegt allein in
 * den Argumenten — und die dürfen hier nicht stehen (Serverpfade, Dateinamen,
 * IPs).
 */
function AiWartezeile({ aufrufe }: { aufrufe: AiToolPlanAufruf[] }) {
  const { t } = useTranslation()
  const namen = [...new Set(aufrufe.map((aufruf) => aufruf.tool_name))]

  if (namen.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-on-surface-variant">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
        {t('ai.chat.thinking')}
      </p>
    )
  }
  return (
    <div className="space-y-1">
      {namen.map((name) => (
        <p key={name} className="flex items-center gap-2 text-sm text-on-surface-variant">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
          {/* Der Name kommt vom Modell und ist damit Fremdeingabe. Er wird
              übersetzt oder gar nicht gezeigt: der Rückfall ist der
              allgemeine Satz, nie der rohe Werkzeugname. */}
          {t(`ai.toolsRunning.${name}`, { defaultValue: t('ai.chat.working') })}
        </p>
      ))}
    </div>
  )
}

/**
 * Ein Verlauf, den man **liest**.
 *
 * Kein Bearbeiten, keine Anhänge, kein Eingabefeld — das ist der Unterschied
 * zur Schleife in `AiChat`, und er ist Absicht. Eine getippte Nachricht löst
 * über `vorgaenger_abloesen` jeden offenen Lauf ihrer Unterhaltung ab; in
 * einem Fenster, in dem seit vier Uhr eine Reparatur läuft, wäre ein
 * Eingabefeld ein Knopf zum versehentlichen Abbrechen.
 *
 * **Vorschlagskarten bleiben trotzdem bedienbar.** Eine Karte zu bestätigen
 * ist keine Nachricht: sie löst nichts ab, sondern weckt den geparkten Lauf
 * genau dort, wo er steht. Wer gerade im Panel sitzt, soll nicht auf die
 * E-Mail warten müssen.
 */
export function AiVerlauf({ entries, laufendeWerkzeuge, onProposalChange }: {
  entries: Entry[]
  laufendeWerkzeuge: AiToolPlanAufruf[]
  onProposalChange: (proposal: AiActionProposal) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-4">
      {entries.map((entry) => {
        if (entry.kind === 'compacted') {
          return (
            <div key={entry.id} className="flex items-center gap-3 py-2">
              <span className="h-px flex-1 bg-outline-variant/40" />
              <span className="text-xs text-on-surface-variant">{t('ai.chat.compacted')}</span>
              <span className="h-px flex-1 bg-outline-variant/40" />
            </div>
          )
        }
        if (entry.kind === 'proposal') {
          return (
            <AiActionProposalCard
              key={entry.id}
              proposal={entry.proposal}
              onChange={onProposalChange}
            />
          )
        }

        const { message } = entry
        if (message.role === 'user') {
          // Im Guardian-Fenster ist die „Benutzernachricht" der Auftragstext,
          // den das Panel geschrieben hat — kein Mensch. Sie steht trotzdem
          // wie eine: sie ist der Anlass, aus dem alles Folgende entstand.
          return (
            <article key={entry.id} className="flex justify-end gap-3">
              <div className="flex max-w-[85%] flex-col items-end rounded-2xl rounded-br-md border border-primary/25 bg-primary/10 px-4 py-2.5">
                <p className="w-full whitespace-pre-wrap break-words text-sm leading-6 text-on-surface">
                  {message.content}
                </p>
                {message.created_at && (
                  <span className="mt-1 text-[10px] text-on-surface-variant/70">
                    {formatMessageTime(message.created_at)}
                  </span>
                )}
              </div>
              <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-container-high text-on-surface-variant">
                <User className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
            </article>
          )
        }

        return (
          <AiAntwortblase
            key={entry.id}
            message={message}
            beantwortet
            busy={false}
            laufendeWerkzeuge={
              message.status === 'streaming' ? laufendeWerkzeuge : KEINE_AUFRUFE
            }
            onAnswer={null}
          />
        )
      })}
    </div>
  )
}
