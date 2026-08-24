import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Brain, Check, ListPlus, Loader2, Paperclip, Pencil, Send, Sparkles, Square, Trash2, User, X, Zap } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  type AiAttachment,
  type AiContextStatus,
  type AiProviderAvailable,
  type AiRunInfo,
} from '@/api/ai'
import { api, SanitizedApiError } from '@/api/client'
import { Button, Dropdown, type DropdownOption } from '@/Singra/UI'
import {
  aiChatPreferenceKeys,
  readAiProviderChoice,
  readAiReasoningChoice,
  writeAiProviderChoice,
  writeAiReasoningChoice,
} from '@/lib/aiChatPreferences'
import { browserWahlInsKontoUebernehmen } from '@/lib/aiProviderKonto'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { AiActionProposalCard } from './AiActionProposalCard'
import { AiAutonomyButton } from './AiAutonomyButton'
import { AiContextMeter } from './AiContextMeter'
import { AiMemoryNotice } from './AiMemoryNotice'
// Die Blase und ihre Bestandteile standen hier, solange es genau ein Fenster
// gab. Sie liegen jetzt in `AiVerlauf`, weil das Guardian-Fenster denselben
// Verlauf zeichnet — zwei Zeichner wären zwei Wahrheiten darüber, wie ein Zug
// der KI aussieht. Die Schleife hier bleibt: an ihr hängen Bearbeiten und
// Anhänge, und die gehören nicht in einen Verlauf, den man nur liest.
import { AiAntwortblase, formatMessageTime, KEINE_AUFRUFE, mergeEntries } from './AiVerlauf'
import { WorkerLeiste } from './WorkerLeiste'
import { useAiLauf } from './useAiLauf'
import { AI_ZUSTELLUNG_EVENT } from '@/lib/aiZustellung'
import { useHasPermission } from '@/hooks/useHasPermission'

interface ServerOption {
  id: number
  name: string
}

const ATTACHMENT_ACCEPT = '.txt,.log,.cfg,.conf,.ini,.json,.properties,.toml,.yaml,.yml,.png,.jpg,.jpeg'

/**
 * Wie oft das Tipp-Signal höchstens gesendet wird.
 *
 * Die Ruhe-Karenz der Meldestelle liegt bei 10–20 Sekunden
 * (docs/agentic-framework.md, §4) — ein Signal je zehn Sekunden hält sie
 * zuverlässig offen, ohne je Tastendruck einen Request zu erzeugen.
 * Übertragen wird nur der Zeitpunkt, nie der Text.
 */
const TIPP_TAKT_MS = 10_000

/**
 * Wie oft der offene Chat nach Fremdem sieht — Nachrichten aus einem zweiten
 * Tab, der Desktop-App oder einer Meldestellen-Lieferung. Derselbe Abstand
 * wie im Guardian-Fenster, aus demselben Grund: nachsehen, nicht streamen.
 */
const NACHSEHEN_MS = 20_000

/**
 * Die Denkwahl — dieselben zwei Felder, die auch auf der Leitung stehen und in
 * `ai_runs` landen: **ob** nachgedacht wird und **wie tief**.
 *
 * Zwei Felder statt eines, weil die Anbieter selbst zwei Dinge kennen: 145 der
 * 272 denkenden Modelle nennen ueberhaupt keine Stufen. `{an: true, stufe:
 * null}` heisst dort „denk nach, so wie du es fuer richtig haeltst" — mit einem
 * einzelnen Stufenfeld muesste man dafuer einen Wert erfinden.
 */
interface Denkwahl {
  an: boolean
  stufe: string | null
}

/**
 * Die zwei Eintraege der Auswahlliste, die keine Stufe sind.
 *
 * Unser `Dropdown` traegt Zeichenketten als Werte; `null` waere dort „nichts
 * gewaehlt" und wuerde den Platzhalter zeigen statt einer Entscheidung. Beide
 * Werte koennen keine echte Stufe verdecken: `ai_reasoning.RANGFOLGE` kennt sie
 * nicht, und `none` — das Wort, das OpenRouter fuer „aus" in die Stufenliste
 * schreibt — filtert der Server bereits heraus.
 */
const AUS = '__aus__'
const AN_OHNE_STUFE = '__an__'

function wahlAusOption(wert: string): Denkwahl {
  if (wert === AUS) return { an: false, stufe: null }
  if (wert === AN_OHNE_STUFE) return { an: true, stufe: null }
  return { an: true, stufe: wert }
}

/**
 * Die Denkwahl beim Providerwechsel auf etwas Gueltiges bringen.
 *
 * Jedes Modell kennt andere Stufen — „xhigh" beim einen gibt es beim naechsten
 * nicht. Bliebe die alte Wahl stehen, senkte der Server sie stillschweigend,
 * und die Oberflaeche zeigte etwas anderes an, als tatsaechlich gilt. Bei einem
 * Modell mit Denkzwang ist ausserdem „aus" keine gueltige Wahl.
 *
 * Eine reine Funktion, damit sie sich ohne gerendertes Bauteil pruefen laesst.
 */
function denkwahlFuer(jetzt: Denkwahl, provider: AiProviderAvailable): Denkwahl {
  if (!provider.reasoning) return { an: false, stufe: null }
  // Ausgeschaltet lassen, wo das Modell das zulaesst — auch beim Wechsel. Eine
  // stillschweigend wieder eingeschaltete Denktiefe kostet Geld.
  if (!jetzt.an && provider.can_disable) return { an: false, stufe: null }
  if (provider.efforts.length === 0) return { an: true, stufe: null }
  if (jetzt.stufe && provider.efforts.includes(jetzt.stufe)) return { an: true, stufe: jetzt.stufe }
  return { an: true, stufe: provider.default_effort ?? provider.efforts[0] }
}

/**
 * Mit welchem Modell der Chat aufgeht: dem zuletzt gewaehlten, sonst dem
 * ersten benutzbaren.
 *
 * Die gemerkte Kennung wird nicht geglaubt, sondern gegen die Liste geprueft.
 * Zwischen zwei Besuchen kann der Provider geloescht, sein Schluessel entfernt
 * oder dem Benutzer die Rolle entzogen worden sein — dann steht er nicht mehr
 * als `available` in der Liste, und ein Verweis darauf ergaebe eine
 * Auswahlliste, deren angezeigter Wert in keiner ihrer Optionen vorkommt.
 *
 * Eine reine Funktion, damit sie sich ohne gerendertes Bauteil pruefen laesst.
 */
function providerBeimOeffnen(
  providers: AiProviderAvailable[],
  gemerkt: number | null,
): number | null {
  const benutzbar = providers.filter((item) => item.available)
  if (gemerkt !== null && benutzbar.some((item) => item.id === gemerkt)) return gemerkt
  return benutzbar[0]?.id ?? null
}

/**
 * Der KI-Assistent: **eine** Unterhaltung, die die Seite ausfuellt.
 *
 * Bewusst wie ein Messenger und nicht wie ein Verwaltungsformular. Es gibt
 * keinen Weg, einen zweiten Chat anzulegen — der Assistent ist ein
 * Gespraechspartner, keine Ablage. Alles Weitere (Provider, Denkschritte,
 * autonomer Modus, Skills) haengt als Schalter am Chat statt in eigenen
 * Kaesten daneben.
 */
export function AiChat() {
  const { t } = useTranslation()
  const canAttach = useHasPermission('ai.attachments.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const canUseMemory = useHasPermission('ai.memory.use')
  // Modell und Denkstufe merkt sich der Browser — je Benutzer, begruendet in
  // `aiChatPreferences`. Die Kennung kommt aus dem Auth-Store statt aus einer
  // Prop, damit keine Einbindung sie vergessen kann.
  const userId = useAuthStore((state) => state.user?.id ?? 'anonym')
  const merkSchluessel = useMemo(() => aiChatPreferenceKeys(userId), [userId])

  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [attachments, setAttachments] = useState<AiAttachment[]>([])
  const [servers, setServers] = useState<ServerOption[]>([])
  // Zwei Felder, genau wie auf der Leitung und in `ai_runs`: **ob** nachgedacht
  // wird und **wie tief**. Das ist keine Doppelung — 145 der 272 denkenden
  // Modelle kennen ueberhaupt keine Stufen, dort ist `stufe` zwangslaeufig
  // `null`, und ein einzelnes Feld muesste fuer sie einen Wert erfinden.
  // Welche Stufen es gibt, sagt der Provider aus dem Katalog; sie sind je
  // Modell verschieden und bereits auf die Rolle dieses Benutzers geklemmt.
  // Die Wahl ueberlebt das Neuladen: sie steht im localStorage unter der
  // Benutzerkennung. Der Anfangswert kommt deshalb aus einer Funktion — sonst
  // stuende beim ersten Bild „kein Nachdenken", und der Effekt darunter setzte
  // die gemerkte Stufe erst nach dem Laden der Provider nach.
  const [denken, setDenken] = useState<Denkwahl>(
    () => readAiReasoningChoice(merkSchluessel.reasoning) ?? { an: false, stufe: null },
  )
  // Ob der Einwilligungshinweis faellig ist. Die 24-Stunden-Regel entscheidet
  // das Backend — hier steht nur das Ergebnis.
  const [memoryNoticeDue, setMemoryNoticeDue] = useState(false)
  const [input, setInput] = useState('')
  // Welche eigene Nachricht gerade umformuliert wird, und womit.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  // Was beim Oeffnen schon lief. Wird in einem eigenen Effekt angehaengt, weil
  // das Anhaengen erst gehen kann, wenn die Verarbeitung steht.
  const [laufBeimOeffnen, setLaufBeimOeffnen] = useState<AiRunInfo | null>(null)
  // Wie voll der Kontext ist. `null` heisst „noch nicht geladen oder nicht
  // abrufbar“ — der Ring bleibt dann schlicht weg.
  const [contextStatus, setContextStatus] = useState<AiContextStatus | null>(null)
  // Steht der Verlauf unten? Nur dann wird nachgeschoben. Anfangs ja — ein
  // frisch geöffneter Chat zeigt die letzte Nachricht.
  const [amEnde, setAmEnde] = useState(true)
  // Warteschlange fuer Nachrichten, die waehrend des Streams eingegeben werden.
  const [queuedMessages, setQueuedMessages] = useState<string[]>([])

  const verlaufRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef(true)
  const dragDepthRef = useRef(0)
  // Wann zuletzt „der Mensch tippt" gemeldet wurde — die Drossel des
  // Tipp-Signals. Ein Ref und kein State: der Wert soll nichts neu zeichnen.
  const letztesTippSignalRef = useRef(0)
  // Welches Modell **jetzt** gewählt ist. `ladeKontext` braucht das, um eine
  // Antwort zu erkennen, die zu einer älteren Wahl gehört. Zuweisung beim
  // Render, dasselbe Muster wie `autoscrollRef` in ServerConsolePanel.
  const providerRef = useRef<number | null>(null)
  providerRef.current = providerId

  useEffect(() => {
    // StrictMode fuehrt Setup/Cleanup in Entwicklung absichtlich doppelt aus.
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      aiApi.listProviders(),
      aiApi.getConversation(),
      aiApi.listActions(),
      canAttach ? aiApi.listAttachments() : Promise.resolve([] as AiAttachment[]),
      // Nur für den Autonomie-Knopf, und nur dort wird die Liste gebraucht.
      // Ohne das Recht holte jedes Öffnen der Seite alle sichtbaren Server
      // samt Bind-IP, Spiel-, Query- und RCON-Port in den Browser — für eine
      // Auswahlliste, die gar nicht gezeichnet wird.
      canUseAutonomy
        ? api<ServerOption[]>('/servers').catch(() => [] as ServerOption[])
        : Promise.resolve([] as ServerOption[]),
      // Scheitert der Abruf, wird der Hinweis nicht gezeigt statt den ganzen
      // Chat scheitern zu lassen — er ist wichtig, aber nicht so wichtig.
      canUseMemory
        ? aiApi.getMemoryPreference().catch(() => null)
        : Promise.resolve(null),
      // Laeuft da noch etwas von vorhin? Scheitert die Frage, wird eben nicht
      // angehaengt — der Verlauf steht ohnehin.
      aiApi.getActiveRun().catch(() => null),
    ])
      .then(([providerRows, conversation, actions, attachmentRows, serverRows, memoryPreference, aktiverLauf]) => {
        if (!active) return
        setLaufBeimOeffnen(aktiverLauf)
        setMemoryNoticeDue(Boolean(memoryPreference?.notice_due))
        setProviders(providerRows)
        // Die Wahl am Konto schlägt die im Browser: sie ist die eine Quelle,
        // die auch Overlay und Desktop-App sehen. Der localStorage bleibt als
        // Rückfall für Konten, die noch nie hier gewählt haben. Bewusst
        // `getState()` statt Abo — die gespeicherte Wahl ändert sich beim
        // Wechseln unten, und ein Abo lüde dann den ganzen Chat neu.
        const kontoWahl = useAuthStore.getState().user?.ai_provider_id ?? null
        setProviderId(providerBeimOeffnen(
          providerRows,
          kontoWahl ?? readAiProviderChoice(merkSchluessel.provider),
        ))
        // Einmalige Übernahme: eine Browser-Wahl aus der Zeit vor dem
        // Konto-Feld wandert beim ersten Öffnen ans Konto. Ohne das spräche
        // das Overlay bis zum nächsten manuellen Modellwechsel weiter mit dem
        // erstbesten Zugang — und der kann beliebig langsam sein.
        void browserWahlInsKontoUebernehmen()
        // Vorschlaege werden chronologisch zwischen die Nachrichten einsortiert,
        // damit man sieht, auf welche Antwort sie sich beziehen. Vorher standen
        // sie gesammelt am Ende und wirkten losgeloest.
        setEntries(mergeEntries(conversation.messages, actions))
        setAttachments(attachmentRows)
        setServers(serverRows.map((row) => ({ id: row.id, name: row.name })))
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canAttach, canUseAutonomy, canUseMemory, merkSchluessel.provider, t])

  /**
   * Die Zahlen hinter dem Ring — abhaengig vom **Modell**, nicht nur vom Chat.
   *
   * Deshalb neu geholt, sobald der Provider wechselt: dasselbe Gespraech fuellt
   * ein 128k-Fenster fast und ein 1M-Fenster kaum. Ohne das zeigte der Ring nach
   * einem Modellwechsel weiter den Fuellstand des alten.
   */
  const ladeKontext = useCallback(async () => {
    if (!providerId) {
      setContextStatus(null)
      return
    }
    try {
      const status = await aiApi.getContextStatus(providerId)
      // Nur übernehmen, wenn die Wahl seither dieselbe geblieben ist. Zwei
      // Modellwechsel kurz hintereinander können sich überholen — die Auskunft
      // ist bei kaltem Modellkatalog ein externer Abruf. Der Ring zeigte dann
      // das Fenster des ersten Modells für das zweite an, und genau daran
      // liest der Benutzer ab, wann gefaltet wird.
      if (mountedRef.current && providerRef.current === providerId) setContextStatus(status)
    } catch {
      // Der Ring ist eine Zusatzauskunft. Faellt sie aus, verschwindet er —
      // ein Fehlertoast fuer eine Anzeige waere laestiger als die fehlende Zahl.
      if (mountedRef.current) setContextStatus(null)
    }
  }, [providerId])

  useEffect(() => { void ladeKontext() }, [ladeKontext])

  /**
   * Der Lauf und sein Ereignisstrom — der Verlauf entsteht dort, nicht hier.
   *
   * Diese Komponente bleibt für das zuständig, was man sieht und bedient:
   * Modellwahl, Denktiefe, Anhänge, Bearbeiten, Zeichnen. Wie aus dreizehn
   * Ereignisarten ein Verlauf wird, steht in `useAiLauf` und lässt sich dort
   * ohne gerendertes Bauteil prüfen.
   */
  const {
    entries, setEntries, streaming, laufendeWerkzeuge, runId, setRunId,
    merkeVorschlag, sendContent, haengeAn, stoppeLauf,
  } = useAiLauf({ providerId, canAttach, denken, ladeKontext, setAttachments })

  /**
   * Nachschieben — aber nur, solange der Verlauf unten steht.
   *
   * Vorher zog jedes Textstück die Ansicht ans Ende. Wer während einer langen
   * Antwort hochscrollte, um einen früheren Absatz oder eine Werkzeugzeile zu
   * lesen, wurde vom nächsten Stück sofort zurückgerissen. Dasselbe Muster
   * steht in `ServerConsolePanel`.
   */
  useEffect(() => {
    const kasten = verlaufRef.current
    if (kasten && amEnde) kasten.scrollTop = kasten.scrollHeight
  }, [amEnde, entries])

  const availableProviders = useMemo(
    () => providers.filter((provider) => provider.available),
    [providers],
  )

  const aktiverProvider = useMemo(
    () => availableProviders.find((provider) => provider.id === providerId) ?? null,
    [availableProviders, providerId],
  )

  // Beim Providerwechsel die Wahl auf etwas Gueltiges bringen. Warum das noetig
  // ist, steht bei `denkwahlFuer`.
  useEffect(() => {
    if (!aktiverProvider) return
    setDenken((jetzt) => denkwahlFuer(jetzt, aktiverProvider))
  }, [aktiverProvider])

  /**
   * Die Wahl merken — aber nur die **gewaehlte**, nicht die zurechtgebogene.
   *
   * Deshalb hier und nicht in einem Effekt auf `denken`: der Effekt darueber
   * senkt die Stufe beim Wechsel auf ein Modell, das sie nicht kennt. Schriebe
   * man auch das mit, waere „xhigh" nach einem kurzen Abstecher zu einem
   * kleineren Modell dauerhaft verloren, ohne dass jemand es angefasst hat.
   */
  const waehleDenken = useCallback((wahl: Denkwahl) => {
    setDenken(wahl)
    writeAiReasoningChoice(merkSchluessel.reasoning, wahl)
  }, [merkSchluessel.reasoning])

  /**
   * Das Modell merken. Auch hier nur die gewaehlte Kennung — was beim naechsten
   * Oeffnen daraus wird, entscheidet `providerBeimOeffnen` gegen die dann
   * gueltige Liste.
   *
   * Gemerkt wird am **Konto** (PATCH), nicht nur im Browser: Overlay und
   * Desktop-App kennen den localStorage dieses Fensters nicht und liefen sonst
   * stillschweigend auf einem anderen Modell als das Panel. Der localStorage
   * bleibt als Rueckfall, falls der Server die Wahl gerade nicht annimmt —
   * dann gilt sie wenigstens in diesem Fenster weiter.
   */
  const waehleProvider = useCallback((wert: string) => {
    const id = Number(wert)
    setProviderId(id)
    writeAiProviderChoice(merkSchluessel.provider, id)
    void api<{ ai_provider_id: number | null }>('/auth/me/ai-provider', {
      method: 'PATCH',
      body: JSON.stringify({ provider_id: id }),
    })
      .then((antwort) => useAuthStore.getState().updateUser({ ai_provider_id: antwort.ai_provider_id }))
      .catch(() => undefined)
  }, [merkSchluessel.provider])

  /** Hochgeladen, aber noch nicht abgeschickt — die Chips über dem Eingabefeld. */
  const offeneAnhaenge = useMemo(
    () => attachments.filter((item) => item.message_id === null),
    [attachments],
  )

  /** Anhänge nach der Nachricht, mit der sie gesendet wurden. */
  const anhaengeJeNachricht = useMemo(() => {
    const karte = new Map<string, AiAttachment[]>()
    for (const item of attachments) {
      if (!item.message_id) continue
      const liste = karte.get(item.message_id)
      if (liste) liste.push(item)
      else karte.set(item.message_id, [item])
    }
    return karte
  }, [attachments])

  const uploadAttachment = useCallback(async (file: File | undefined) => {
    if (!file || streaming || uploading) return
    setUploading(true)
    try {
      const created = await aiApi.uploadAttachment(file)
      // Nach Kennung einsetzen statt anhaengen: das Nachladen nach dem Absenden
      // (siehe `message`-Ereignis) kann eine Antwort ueberholen, die noch
      // unterwegs war. Beim blossen Anhaengen stuende die Datei dann zweimal in
      // der Liste — React beschwert sich ueber den doppelten Key, und der
      // Benutzer sieht einen Anhang, den er nur einmal hochgeladen hat.
      if (mountedRef.current) {
        setAttachments((current) => [
          ...current.filter((item) => item.id !== created.id),
          created,
        ])
      }
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.attachments.error'))
    } finally {
      if (mountedRef.current) setUploading(false)
    }
  }, [streaming, t, uploading])

  const removeAttachment = async (attachment: AiAttachment) => {
    try {
      await aiApi.deleteAttachment(attachment.id)
      setAttachments((current) => current.filter((item) => item.id !== attachment.id))
    } catch {
      toast.error(t('ai.attachments.error'))
    }
  }

  const clearHistory = async () => {
    if (streaming) return
    const accepted = await confirm({
      title: t('ai.chat.clearTitle'),
      message: t('ai.chat.clearConfirm'),
      confirmText: t('ai.chat.clear'),
      danger: true,
    })
    if (!accepted) return
    try {
      await aiApi.clearHistory()
      setEntries([])
      setAttachments([])
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.delete'))
    }
  }

  // Abarbeiten der Warteschlange, sobald der vorherige Lauf beendet ist
  useEffect(() => {
    if (!streaming && queuedMessages.length > 0 && providerId) {
      const nextContent = queuedMessages[0]
      setQueuedMessages((q) => q.slice(1))
      void sendContent(nextContent)
    }
  }, [providerId, queuedMessages, sendContent, streaming])

  const enqueueMessage = useCallback((content: string) => {
    const text = content.trim()
    if (!text) return
    setQueuedMessages((q) => [...q, text])
    setInput('')
  }, [])

  const sendImmediatelyAndInterrupt = useCallback(async (content: string) => {
    const text = content.trim()
    if (!text || !providerId) return
    setInput('')
    if (streaming) {
      await stoppeLauf()
    }
    await sendContent(text)
  }, [providerId, sendContent, stoppeLauf, streaming])

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    const content = input.trim()
    if (!content || !providerId) return
    if (streaming) {
      enqueueMessage(content)
      return
    }
    setInput('')
    await sendContent(content)
  }

  /**
   * Nimmt eine bereits gesendete eigene Nachricht zurück und stellt sie neu.
   *
   * Zwei Schritte, weil sie zwei verschiedene Dinge sind: der Schnitt räumt
   * den Verlauf ab dieser Nachricht auf — sie selbst eingeschlossen —, und
   * erst danach geht der neue Text den gewohnten Weg. Die KI sieht von der
   * alten Fassung nichts mehr; sie steht weder im Verlauf noch im Kontext.
   */
  const submitEdit = async (messageId: string) => {
    const content = editDraft.trim()
    if (!content || !providerId || streaming) return
    try {
      await aiApi.editMessage(messageId, content)
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.edit'))
      return
    }
    // Erst wenn der Schnitt durch ist: sonst stünde die alte Fassung noch da,
    // während der Server sie schon nicht mehr kennt.
    setEntries((current) => {
      const index = current.findIndex((item) => item.kind === 'message' && item.id === messageId)
      return index === -1 ? current : current.slice(0, index)
    })
    setEditingId(null)
    setEditDraft('')
    await sendContent(content)
  }

  /**
   * Beim Oeffnen an einen laufenden Lauf anhaengen.
   *
   * Das ist die andere Haelfte von "der Lauf haengt an nichts": er arbeitet
   * weiter, waehrend man woanders ist — und wenn man zurueckkommt, sieht man
   * ihn wieder. Ohne das stuende hier eine abgebrochene Antwort.
   */
  useEffect(() => {
    if (!laufBeimOeffnen) return
    setLaufBeimOeffnen(null)
    if (!laufBeimOeffnen.live) return
    setRunId(laufBeimOeffnen.id)
    void haengeAn(laufBeimOeffnen.id)
  }, [haengeAn, laufBeimOeffnen, setRunId])

  /**
   * Fremdes nachladen — auf Zuruf **und** im Takt.
   *
   * Zuruf ist das Zustell-Ereignis der Glocke (Meldestellen-Lieferungen,
   * beobachtete Laufwechsel). Er allein hat nicht gereicht: die Glocke sieht
   * einen Lauf nur, wenn ihr Takt ihn trifft — wer im Chat stand, pollte dort
   * nur alle 60 Sekunden, und ein Lauf aus einem zweiten Tab oder der
   * Desktop-App begann und endete komplett zwischen zwei Blicken. Die
   * Nachricht stand dann in der Datenbank und nirgendwo auf dem Schirm, bis
   * jemand hart neu lud.
   *
   * Deshalb zusätzlich derselbe 20-Sekunden-Takt, den das Guardian-Fenster
   * seit jeher hat (`GuardianAnsicht.NACHSEHEN_MS`): nachsehen, nicht
   * streamen. Er ruht, solange das Fenster verdeckt ist — eine App im Tray
   * soll nicht alle 20 Sekunden das Panel wecken; beim Sichtbarwerden wird
   * einmal sofort nachgeladen.
   *
   * Nicht während eines Stroms: dann ist SSE die Wahrheit, und ein Nachladen
   * mittendrin ersetzte den halb gezeichneten Zug durch seinen alten Stand.
   * Das gilt auch für eine Antwort, die **unterwegs** ist, wenn der Strom
   * beginnt: `veraltet` entwertet sie beim Effekt-Wechsel, sonst überschriebe
   * sie die optimistischen Blasen, und die folgenden Deltas liefen ins Leere
   * (`useAiLauf.aendere` findet die ID dann nicht mehr und schweigt).
   */
  useEffect(() => {
    if (streaming) return
    let veraltet = false
    const nachladen = () => {
      void Promise.all([aiApi.getConversation(), aiApi.listActions()])
        .then(([conversation, actions]) => {
          if (veraltet || !mountedRef.current) return
          setEntries(mergeEntries(conversation.messages, actions))
        })
        .catch(() => undefined)
    }
    const takt = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') nachladen()
    }, NACHSEHEN_MS)
    const sichtbar = () => {
      if (document.visibilityState === 'visible') nachladen()
    }
    window.addEventListener(AI_ZUSTELLUNG_EVENT, nachladen)
    document.addEventListener('visibilitychange', sichtbar)
    return () => {
      veraltet = true
      window.clearInterval(takt)
      window.removeEventListener(AI_ZUSTELLUNG_EVENT, nachladen)
      document.removeEventListener('visibilitychange', sichtbar)
    }
  }, [setEntries, streaming])

  /**
   * Das Tipp-Signal: gedrosselt, ohne Inhalt, ohne Fehlerbehandlung nach aussen.
   *
   * Die Meldestelle hält Worker-Meldungen zurück, solange der Mensch schreibt
   * (Ruhe-Regel). Was sie dafür braucht, ist genau ein Zeitstempel — der Text
   * verlässt den Browser nicht, nicht einmal seine Länge.
   */
  const tippSignal = useCallback(() => {
    const jetzt = Date.now()
    if (jetzt - letztesTippSignalRef.current < TIPP_TAKT_MS) return
    letztesTippSignalRef.current = jetzt
    void aiApi.typing().catch(() => undefined)
  }, [])

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center" aria-label={t('common.loading')}>
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  const busy = streaming || uploading
  const empty = entries.length === 0

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      aria-label={t('ai.chat.title')}
      onDragEnter={(event) => {
        if (!canAttach || busy) return
        event.preventDefault()
        dragDepthRef.current += 1
        setDragging(true)
      }}
      onDragOver={(event) => { if (canAttach && !busy) event.preventDefault() }}
      onDragLeave={() => {
        // Zaehler statt Boolean: das Verlassen eines Kindelements feuert
        // ebenfalls `dragleave` und wuerde die Flaeche sonst flackern lassen.
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
        if (dragDepthRef.current === 0) setDragging(false)
      }}
      onDrop={(event) => {
        if (!canAttach || busy) return
        event.preventDefault()
        dragDepthRef.current = 0
        setDragging(false)
        void uploadAttachment(event.dataTransfer.files?.[0])
      }}
    >
      {/* ── Kopfzeile: Provider, Denkschritte, Autonomie, Skills ───────── */}
      <header className="flex flex-wrap items-center gap-2 border-b border-outline-variant/40 px-3 py-2 sm:px-4">
        <div className="min-w-[10rem] max-w-[16rem] flex-1">
          <Dropdown
            value={providerId ? String(providerId) : null}
            onChange={waehleProvider}
            options={availableProviders.map((provider) => ({
              value: String(provider.id),
              label: provider.name,
              hint: provider.default_model,
            }))}
            placeholder={t('ai.chat.selectProvider')}
            disabled={busy}
            aria-label={t('ai.chat.selectProvider')}
          />
        </div>

        <ReasoningPicker
          provider={aktiverProvider}
          wahl={denken}
          onChange={waehleDenken}
          disabled={busy}
        />

        {canUseAutonomy && <AiAutonomyButton servers={servers} disabled={busy} />}

        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button" variant="ghost" size="sm"
            disabled={busy || empty}
            onClick={() => void clearHistory()}
            aria-label={t('ai.chat.clear')}
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </header>

      {/* ── Die Worker-Leiste: lebende Hintergrund-Aufträge, einsehbar ── */}
      <WorkerLeiste />

      {/* ── Verlauf ───────────────────────────────────────────────────── */}
      <div
        ref={verlaufRef}
        className="relative min-h-0 flex-1 overflow-y-auto"
        aria-live="polite"
        onScroll={(event) => {
          // Die 50 Pixel Spielraum sind derselbe Wert wie in der Konsole: wer
          // fast unten steht, meint unten.
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget
          setAmEnde(scrollHeight - scrollTop - clientHeight < 50)
        }}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary/60 bg-primary/5">
            <span className="flex items-center gap-2 text-sm font-medium text-primary">
              <Paperclip className="h-4 w-4" aria-hidden="true" />
              {t('ai.attachments.drop')}
            </span>
          </div>
        )}

        <div className="mx-auto w-full max-w-3xl px-3 py-6 sm:px-4">
          {empty && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h2 className="mt-4 font-headline text-xl font-semibold text-on-surface">
                {t('ai.chat.emptyTitle')}
              </h2>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.chat.emptyDescription')}
              </p>
              <ul className="mx-auto mt-6 grid max-w-lg gap-2 text-left">
                {['logs', 'network', 'mods'].map((key) => (
                  <li key={key}>
                    <button
                      type="button"
                      className="w-full rounded-xl border border-outline-variant/40 bg-surface-container-low/40 px-4 py-3 text-sm text-on-surface-variant transition-colors hover:border-primary/40 hover:text-on-surface"
                      onClick={() => setInput(t(`ai.chat.examples.${key}`))}
                    >
                      {t(`ai.chat.examples.${key}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-4">
            {entries.map((entry, index) => {
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
                    onChange={(updated) => {
                      merkeVorschlag(updated)
                      // **Hier ging es frueher nicht weiter.** Die Aktion lief,
                      // und der Chat blieb stumm — man musste eine neue
                      // Nachricht schreiben, damit die KI ueberhaupt erfuhr,
                      // wie ihr eigener Vorschlag ausgegangen ist.
                      const lauf = updated.run_id ?? runId
                      if (lauf && updated.status !== 'proposed') {
                        void haengeAn(lauf)
                      }
                    }}
                  />
                )
              }

              const { message } = entry
              if (message.role === 'user') {
                const isEditing = editingId === message.id
                return (
                  <article key={entry.id} className="group flex justify-end gap-3">
                    {isEditing ? (
                      <div className="w-full max-w-[85%] space-y-2">
                        <textarea
                          className="msm-input min-h-[4.5rem] w-full text-sm"
                          value={editDraft}
                          maxLength={16_000}
                          autoFocus
                          disabled={busy}
                          onChange={(event) => setEditDraft(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Escape') { setEditingId(null); setEditDraft('') }
                            if (event.key === 'Enter' && !event.shiftKey) {
                              event.preventDefault()
                              void submitEdit(message.id)
                            }
                          }}
                          aria-label={t('ai.chat.edit')}
                        />
                        {/* Der Hinweis gehoert hierher, nicht in eine Rueckfrage
                            danach: wer bearbeitet, soll vorher wissen, dass der
                            Verlauf ab hier verschwindet. */}
                        <p className="text-xs text-on-surface-variant">{t('ai.chat.editHint')}</p>
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button" size="sm" variant="secondary" disabled={busy}
                            onClick={() => { setEditingId(null); setEditDraft('') }}
                          >
                            <X className="h-4 w-4" aria-hidden="true" />
                            {t('common.cancel')}
                          </Button>
                          <Button
                            type="button" size="sm"
                            disabled={busy || !editDraft.trim()}
                            onClick={() => void submitEdit(message.id)}
                          >
                            <Check className="h-4 w-4" aria-hidden="true" />
                            {t('ai.chat.editSend')}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => { setEditingId(message.id); setEditDraft(message.content) }}
                          className="mt-1 self-start rounded-lg p-1.5 text-on-surface-variant opacity-0 transition-opacity hover:text-on-surface focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-0"
                          aria-label={t('ai.chat.edit')}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                        <div className="flex max-w-[85%] flex-col items-end rounded-2xl rounded-br-md border border-primary/25 bg-primary/10 px-4 py-2.5">
                          <p className="w-full whitespace-pre-wrap break-words text-sm leading-6 text-on-surface">
                            {message.content}
                          </p>
                          {/* Die Anhaenge stehen **in** der Nachricht, mit der
                              sie gesendet wurden. Vorher hingen sie nur an der
                              Unterhaltung: nach einem Neuladen war nicht mehr
                              erkennbar, zu welcher Frage sie gehoerten. */}
                          <AnhangListe
                            anhaenge={anhaengeJeNachricht.get(message.id) ?? []}
                            t={t}
                          />
                          {message.created_at && (
                            <span className="mt-1 text-[10px] text-on-surface-variant/70">
                              {formatMessageTime(message.created_at)}
                            </span>
                          )}
                        </div>
                      </>
                    )}
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
                  // Beantwortet ist eine Rückfrage, sobald irgendein späterer
                  // Eintrag existiert: dann hat der Benutzer geschrieben, ob
                  // per Knopf oder frei getippt. Damit überlebt der Zustand
                  // auch ein Neuladen der Seite, ohne dass er irgendwo
                  // gespeichert werden müsste.
                  beantwortet={index < entries.length - 1}
                  busy={busy}
                  // Nur die noch schreibende Blase bekommt den Plan. Alle
                  // anderen bekommen immer dieselbe leere Liste — sonst
                  // entwertete eine je Runde neue Eigenschaft den `memo`
                  // Vergleich für den ganzen Verlauf.
                  laufendeWerkzeuge={
                    message.status === 'streaming' ? laufendeWerkzeuge : KEINE_AUFRUFE
                  }
                  onAnswer={sendContent}
                />
              )
            })}
          </div>
        </div>
      </div>

      {/* ── Eingabe ───────────────────────────────────────────────────── */}
      <form className="border-t border-outline-variant/40 px-3 py-3 sm:px-4" onSubmit={send}>
        {/* Der Hinweis steht ueber dem Eingabefeld, nicht in einer
            Einstellungsseite: er soll dort auftauchen, wo die Entscheidung
            Folgen hat — bevor jemand etwas Persoenliches tippt. */}
        {memoryNoticeDue && (
          <AiMemoryNotice onAnswered={() => setMemoryNoticeDue(false)} />
        )}
        <div className="mx-auto w-full max-w-3xl">
          {/* Nur Ungesendetes: alles Uebrige steht in seiner Nachricht. Vorher
              blieb jeder Anhang hier stehen und ging bei jeder Folgefrage
              erneut an den Anbieter. */}
          {offeneAnhaenge.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2" aria-label={t('ai.attachments.list')}>
              {offeneAnhaenge.map((attachment) => (
                <span
                  key={attachment.id}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-outline-variant/40 bg-surface-container-high px-2.5 py-1 text-xs text-on-surface-variant"
                >
                  <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span className="truncate">{attachment.original_name}</span>
                  <button
                    type="button"
                    className="rounded-sm p-0.5 hover:bg-surface-container-highest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    disabled={busy}
                    onClick={() => void removeAttachment(attachment)}
                    aria-label={t('ai.attachments.remove')}
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {queuedMessages.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-lg border border-outline-variant/30 bg-surface-container-high/60 px-2.5 py-1.5 text-xs text-on-surface-variant">
              <span className="flex items-center gap-1 font-medium text-on-surface">
                <ListPlus className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                Warteschlange ({queuedMessages.length}):
              </span>
              {queuedMessages.map((msg, idx) => (
                <span
                  key={idx}
                  className="inline-flex max-w-[220px] items-center gap-1 rounded bg-surface-container-highest px-2 py-0.5 text-on-surface border border-outline-variant/40"
                  title={msg}
                >
                  <span className="truncate">{msg}</span>
                  <button
                    type="button"
                    className="text-on-surface-variant hover:text-status-danger transition-colors"
                    onClick={() => setQueuedMessages((q) => q.filter((_, i) => i !== idx))}
                    aria-label="Aus Warteschlange entfernen"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              {queuedMessages.length > 1 && (
                <button
                  type="button"
                  className="ml-auto text-[11px] text-on-surface-variant hover:text-status-danger underline transition-colors"
                  onClick={() => setQueuedMessages([])}
                >
                  Alle leeren
                </button>
              )}
            </div>
          )}

          <div className="flex items-end gap-2 rounded-2xl border border-outline-variant/50 bg-surface-container-low/50 p-2 focus-within:border-primary/50">
            {canAttach && (
              <label
                className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-on-surface-variant transition-colors ${
                  uploading ? 'pointer-events-none opacity-50' : 'cursor-pointer hover:bg-surface-container-high hover:text-on-surface'
                }`}
                title={t('ai.attachments.add')}
              >
                {uploading
                  ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Paperclip className="h-4 w-4" aria-hidden="true" />}
                <input
                  type="file" className="sr-only" disabled={uploading} accept={ATTACHMENT_ACCEPT}
                  aria-label={t('ai.attachments.add')}
                  onChange={(event) => {
                    void uploadAttachment(event.target.files?.[0])
                    event.target.value = ''
                  }}
                />
              </label>
            )}
            <textarea
              className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent py-1.5 text-sm leading-6 text-on-surface placeholder:text-on-surface-variant focus:outline-none"
              rows={1}
              maxLength={16000}
              value={input}
              onChange={(event) => {
                setInput(event.target.value)
                // Nur bei tatsächlichem Inhalt: ein geleertes Feld ist kein
                // Schreiben, und die Karenz soll dann normal ablaufen.
                if (event.target.value.trim()) tippSignal()
                // Waechst mit dem Text, wie man es von einem Chat kennt.
                event.target.style.height = 'auto'
                event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`
              }}
              onKeyDown={(event) => {
                // Enter sendet/reiht ein, Shift+Enter macht einen Umbruch. Alt+Enter unterbricht und sendet sofort.
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (event.altKey && streaming) {
                    void sendImmediatelyAndInterrupt(input)
                  } else {
                    void send(event as unknown as React.FormEvent)
                  }
                }
              }}
              placeholder={streaming ? 'Nachricht in Warteschlange einreihen oder mit Alt+Enter sofort senden…' : t('ai.chat.placeholder')}
              disabled={uploading || availableProviders.length === 0}
              aria-label={t('ai.chat.message')}
            />
            {/* Direkt neben dem Absenden, weil dort die Entscheidung faellt:
                wer sieht, dass der Kontext gleich zusammengefasst wird, formuliert
                die naechste Frage anders. In einer Einstellungsseite haette
                dieselbe Zahl niemanden erreicht. */}
            <AiContextMeter status={contextStatus} />
            {streaming ? (
              !input.trim() ? (
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  className="h-9 w-9 shrink-0 rounded-full p-0 flex items-center justify-center bg-status-danger/15 text-status-danger hover:bg-status-danger/25 border border-status-danger/30 transition-colors"
                  onClick={() => void stoppeLauf()}
                  aria-label="KI stoppen (abbrechen)"
                  title="KI stoppen (abbrechen)"
                >
                  <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
                </Button>
              ) : (
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    type="button"
                    size="sm"
                    className="h-9 shrink-0 rounded-full px-2.5 text-xs flex items-center gap-1"
                    onClick={() => enqueueMessage(input)}
                    title="In Warteschlange einreihen (Enter)"
                    aria-label="In Warteschlange einreihen"
                  >
                    <ListPlus className="h-3.5 w-3.5" aria-hidden="true" />
                    <span className="hidden sm:inline">Einreihen</span>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    className="h-9 w-9 shrink-0 rounded-full p-0 flex items-center justify-center bg-status-danger/15 text-status-danger hover:bg-status-danger/25 border border-status-danger/30"
                    onClick={() => void sendImmediatelyAndInterrupt(input)}
                    title="Sofort senden & Unterbrechen (Alt+Enter)"
                    aria-label="Sofort senden & Unterbrechen"
                  >
                    <Zap className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              )
            ) : (
              <Button
                type="submit"
                size="sm"
                className="h-9 w-9 shrink-0 rounded-full p-0"
                disabled={uploading || !input.trim() || !providerId}
                aria-label={t('ai.chat.send')}
              >
                <Send className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </div>

          {availableProviders.length === 0 && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-status-warning">
              <Zap className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {t('ai.chat.noProvider')}
            </p>
          )}
          <p className="mt-2 text-center text-xs text-on-surface-variant">{t('ai.chat.privacyHint')}</p>
        </div>
      </form>
    </section>
  )
}

/**
 * Sortiert Vorschlaege chronologisch zwischen die Nachrichten.
 *
 * Beide Listen sind bereits nach `created_at` sortiert; hier werden sie nur
 * zusammengefuehrt. Ein Vorschlag steht damit dort, wo er entstanden ist.
 */
/**
 * Die Anhänge einer Nachricht, unter ihrem Text.
 *
 * Zeigt auch, wenn beim Aufnehmen etwas unkenntlich gemacht wurde. Vorher wurde
 * eine Datei mit einem Tokenmuster komplett abgewiesen — bei echten Serverlogs
 * passiert das ständig. Jetzt wird redigiert, und der Hinweis hier ist der
 * Grund, warum niemand sich über ein `[REDACTED]` im eigenen Log wundern muss.
 */
/**
 * Die Wahl der Denktiefe — je Modell verschieden, deshalb keine feste Liste.
 *
 * Gemessen am 2026-08-11 ueber alle 402 Modelle im OpenRouter-Katalog gibt es
 * genau vier Faelle. Sie fuehren zu **einer** Bedienung, nicht zu vieren:
 *
 * 1. **Modell denkt nicht** (130) → gar keine Anzeige. Ein Regler, der nichts
 *    bewirkt, ist schlimmer als keiner.
 * 2. **Stufen** (127, in 20 verschiedenen Zusammenstellungen) → eine Auswahl
 *    aus genau diesen Stufen. Deshalb kommt die Liste aus dem Katalog und nicht
 *    aus einer Konstante: `gpt-5.6-luna` kennt `max`, `gpt-5.5` hoert bei
 *    `xhigh` auf, `gpt-5.5-pro` faengt erst bei `medium` an.
 * 3. **Nur an/aus** (145) → dieselbe Liste mit zwei Eintraegen. Hier stand
 *    einmal ein `Switch` daneben; das waren zwei Bedienelemente fuer dieselbe
 *    Entscheidung, je nach Modell ein anderes.
 * 4. **Nicht abschaltbar** (82) → „aus" fehlt in der Liste. Der Anbieter denkt
 *    ohnehin und rechnet es ab; ein Aus-Knopf waere gelogen.
 *
 * Die Stufen sind bereits auf die Rolle des Benutzers geklemmt — das erledigt
 * der Server in `ai_reasoning.waehlbare_stufen`. Hier wird nichts entschieden,
 * nur angezeigt.
 */
function ReasoningPicker({ provider, wahl, onChange, disabled }: {
  provider: AiProviderAvailable | null
  wahl: Denkwahl
  onChange: (wahl: Denkwahl) => void
  disabled: boolean
}) {
  const { t } = useTranslation()
  if (!provider?.reasoning) return null

  const optionen: DropdownOption[] = []
  if (provider.can_disable) optionen.push({ value: AUS, label: t('ai.reasoning.off') })
  if (provider.efforts.length === 0) {
    optionen.push({ value: AN_OHNE_STUFE, label: t('ai.chat.reasoning') })
  } else {
    for (const stufe of provider.efforts) {
      optionen.push({ value: stufe, label: t(`ai.reasoning.levels.${stufe}`, { defaultValue: stufe }) })
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      <Brain className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
      <div className="min-w-[7rem] max-w-[11rem]">
        <Dropdown
          value={wahl.an ? (wahl.stufe ?? AN_OHNE_STUFE) : AUS}
          onChange={(gewaehlt) => onChange(wahlAusOption(gewaehlt))}
          options={optionen}
          // Nur eine Option heisst: es gibt nichts zu waehlen. Das kommt bei
          // einem Modell mit Denkzwang und ohne Stufen vor — die Anzeige ist
          // dann eine Auskunft, keine Frage.
          disabled={disabled || optionen.length < 2}
          aria-label={t('ai.chat.reasoningLevel')}
        />
      </div>
    </div>
  )
}

function AnhangListe({ anhaenge, t }: { anhaenge: AiAttachment[]; t: TFunction }) {
  if (anhaenge.length === 0) return null
  return (
    <ul className="mt-2 space-y-1 border-t border-primary/20 pt-2">
      {anhaenge.map((anhang) => (
        <li key={anhang.id} className="flex items-center gap-1.5 text-xs text-on-surface-variant">
          <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
          <span className="truncate">{anhang.original_name}</span>
          {anhang.redacted_spans ? (
            <span className="shrink-0 rounded-full border border-outline-variant/50 px-1.5 py-0.5 text-[10px]">
              {t('ai.attachments.redacted', { count: anhang.redacted_spans })}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
