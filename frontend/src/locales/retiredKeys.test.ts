import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

/**
 * Abgelöste Schlüssel müssen verschwinden, nicht nur unbenutzt herumliegen.
 *
 * Beim Umbau des Gedächtnisbereichs sind `ai.memory.title`, `.description`,
 * `.teamTitle` und `.teamDescription` durch die Bereichsform
 * `ai.memory.titles.<kind>` / `ai.memory.descriptions.<kind>` ersetzt worden —
 * AiMemoryManager.tsx:204-205 bildet den Schlüssel zur Laufzeit aus
 * `scope.kind`. `teams.personalHint` wurde von `teams.personalKnowledgeHint`
 * abgelöst (Teams.tsx). Die alten Zeilen blieben stehen, wortgleich mit ihren
 * Nachfolgern: wer den Teamtext ändert, ändert mit hoher Wahrscheinlichkeit
 * den toten Zwilling und wundert sich, dass die Oberfläche gleich bleibt.
 *
 * Warum eine gepflegte Liste und keine allgemeine „jeder Schlüssel wird
 * benutzt"-Regel: die Oberfläche setzt Schlüssel zur Laufzeit zusammen, eine
 * solche Regel wäre entweder löchrig oder bestünde aus Ausnahmen.
 * `scripts/check-i18n.mjs` prüft die Gegenrichtung (benutzt, aber nicht
 * übersetzt); dies hier ist die fehlende Hälfte.
 *
 * Der ganze Namensraum `permissions` ist entfallen: 34 Kurzbeschriftungen für
 * Rechte, die kein `t()`-Aufruf je gelesen hat, weil der PermissionEditor seine
 * Texte fest verdrahtet im Quelltext trug. Jetzt liest er sie aus
 * `permissionDetails.<schlüssel mit _ statt .>` — und zwei Fassungen desselben
 * Rechtetextes wären schlimmer als die eine deutsche von vorher.
 *
 * `ai.providers.voices` und `ai.providers.realtimeHint` sind am 16.08.2026 mit
 * OpenAIs Realtime-API gefallen. Die acht Stimmen gehörten dem Modell und
 * hatten deshalb eine Beschriftung im Panel („Ash — ruhig, tief"); eine
 * ElevenLabs-Stimme gehört dem Konto des Betreibers, MSM kennt sie nicht und
 * kann sie folglich nicht beschriften. Aus dem Auswahlfeld ist ein Textfeld
 * geworden.
 *
 * Diese beiden sind der Grund, warum es diese Liste gibt: acht wortreiche
 * Hörprofile, die niemand mehr liest, sähen in der Sprachdatei aus wie
 * gepflegte Texte — und der Nächste, der eine Stimme beschreiben will, fände
 * sie und schriebe daran weiter.
 */
const ABGELOESTE_SCHLUESSEL = [
  // 09/2026: vier Schlüssel sagten dasselbe wie `common.apply` und
  // `common.reset` — auf Deutsch Wort für Wort, auf Englisch in vier
  // Fassungen („Apply", „Use it", „Adopt", „Reset"). Der fünfte Zwilling,
  // `ai.guardian.takeOver`, bleibt: „übernehmen" heißt dort nicht
  // „anwenden", sondern „die Steuerung an sich nehmen", und heißt seitdem
  // auch so.
  'profile.timezoneAdopt',
  'ai.providers.recommendationApply',
  'mss.wakeword.zuruecksetzen',
  'databaseConsole.reset',
  'ai.memory.title',
  'ai.memory.description',
  'ai.memory.teamTitle',
  'ai.memory.teamDescription',
  'teams.personalHint',
  'permissions',
  'ai.providers.voices',
  'ai.providers.realtimeHint',
  // 09/2026: die KI hat kein Werkzeug mehr, das den Messenger anfasst. Die
  // Beschriftungen der beiden Suchen und der Text des Rechts
  // `ai.social.message_friend` beschrieben ab da etwas, das es nicht gibt —
  // und ein Rechtetext ist im Rechteeditor eine Zusage, keine Dekoration.
  'ai.tools.search_messenger_contacts',
  'ai.tools.search_messenger_groups',
  'ai.toolsRunning.search_messenger_contacts',
  'ai.toolsRunning.search_messenger_groups',
  'permissionDetails.ai_social_message_friend',
  // 09/2026: 342 Schlüssel, die kein Aufruf je gelesen hat (11 % der Datei),
  // gefunden mit scripts/find-orphan-keys.mjs. Nicht alle stehen hier — eine
  // Liste mit 342 Zeilen wäre selbst Ballast. Hier steht, was jemand aus
  // Versehen neu anlegen würde, weil ein lebender Zwilling daneben liegt:
  //
  // `verifyEmail.*` beschrieb eine Seite, die es nie gab. Der echte Ablauf
  // liegt unter `setup.verifyEmail*` — wer die Bestätigungsseite anfasst,
  // findet über die Suche sonst fünf plausible Schlüssel, die nirgends
  // erscheinen.
  'verifyEmail.title',
  'verifyEmail.loading',
  'verifyEmail.success',
  'verifyEmail.error',
  'verifyEmail.noToken',
  // `nav.social` ("Social & Hub") hat keine Route in navigation.tsx.
  //
  // `profile.tabs.vault` sieht genauso tot aus und ist es nicht: der Panel hat
  // keinen Tresor-Tab, die Desktop-App benutzt den Schlüssel aber in
  // desktop/Einstellungen.tsx. Er steht hier als Warnung, nicht als Eintrag —
  // wer im Panel aufräumt, muss die Desktop-Oberfläche mitlesen.
  'nav.social',
  // `shell.openUserMenu` neben den lebenden `shell.mainNavigation` und
  // `shell.closeNavigation`.
  'shell.openUserMenu',
  // 09/2026: Die Videonotiz hat keine Wischgeste mehr. Sie hing am
  // Vollbildrahmen und fing die Berührung des Sendeknopfs darin ab; abbrechen
  // und senden gehen jetzt nur noch über die beiden Knöpfe. Ein Hinweis, der
  // eine Geste erklärt, die es nicht gibt, ist schlimmer als keiner.
  'social.videoNote.swipeToLock',
  // 09/2026: Mit GPT-Live trägt OpenAI zwei Sprachwege, und welcher gilt,
  // entscheidet das Modell, nicht der Anbieter. Die Texte stehen seitdem je
  // Weg unter `ai.providers.realtime.wege.<weg>`; die alten Paare aus
  // OpenAI-Text und `google…`-Zwilling hätten für GPT-Live einen dritten
  // Zwilling gebraucht. `reasoningValues` sagte Wort für Wort dasselbe wie
  // `ai.reasoning.levels` und kannte die Stufen von GPT-Live nicht.
  'ai.providers.realtime.title',
  'ai.providers.realtime.googleTitle',
  'ai.providers.realtime.hint',
  'ai.providers.realtime.googleHint',
  'ai.providers.realtime.voice',
  'ai.providers.realtime.googleVoice',
  'ai.providers.realtime.reasoningHint',
  'ai.providers.realtime.googleReasoningHint',
  'ai.providers.realtime.reasoningValues',
  // 09/2026: Der Link zur Szene fiel mit dem neuen Copernicus-Katalog. Seine
  // Adresse lädt bei CREODIAS nur die kleine Vorschau als Datei herunter, die
  // der Reiter schon zeigt — Vollauflösung war das nie. Eine echte
  // Vollansicht bräuchte einen eigenen Weg, etwa den Copernicus Browser.
  'ai.geo.openFullScene',
  // 25.09.2026: jede Karte bestätigt nur noch der Klick, auch im Sprachmodus.
  // Die Unterscheidung „sag Ja" / „klick hier" / „klick im Chat" gibt es
  // nicht mehr; `ai.voice.vorschlag.hint` sagt jetzt das eine.
  'ai.voice.vorschlag.hintKlick',
  'ai.voice.vorschlag.hintKlickChat',
]

/** Die Nachfolger muss es geben — sonst wäre das Löschen ein Verlust. */
const NACHFOLGER = [
  'ai.memory.titles.user',
  'ai.memory.titles.team',
  'ai.memory.titles.panel',
  'ai.memory.descriptions.user',
  'ai.memory.descriptions.team',
  'ai.memory.descriptions.panel',
  'teams.personalKnowledgeHint',
  'permissionDetails.users_read.title',
  'permissionDetails.users_read.desc',
  'permissionDetails.server_databases_admin.title',
  'permissionEditor.groups.users',
  // Die Nachfolger des Sprachmodus-Umbaus. `ttsHint` erklärt den Stimmzugang
  // dort, wo `realtimeHint` den Sprachzugang erklärte; die beiden
  // Transkript-Schlüssel sind neu und haben keinen Vorgänger — sie stehen hier
  // trotzdem, weil ein Formularfeld ohne Beschriftung genau so aussieht wie
  // eines, dessen Beschriftung jemand beim Umbau vergessen hat.
  'ai.providers.ttsHint',
  'ai.providers.defaultVoice',
  'ai.providers.defaultVoiceHint',
  'ai.providers.transcriptionModel',
  'ai.providers.transcriptionModelHint',
  'ai.providers.protokoll.tts',
  'ai.providers.protokoll.chat_completions',
  // Der Nachfolger des Wegfalls: die Datenschutzerklärung sagt jetzt
  // ausdrücklich, dass der Messenger für die KI nicht erreichbar ist. Ohne
  // diesen Satz wäre aus der Oberfläche nicht zu erkennen, ob der Zugriff
  // entfernt wurde oder nur unerwähnt blieb.
  'privacyPolicy.sections.ai.items.noMessenger',
  // Die Nachfolger der Sprachweg-Texte — je Weg, den das Backend kennt
  // (`services/ai_voice/sprachwege.py`).
  ...['openai_realtime', 'openai_live', 'gemini_live'].flatMap((weg) =>
    ['title', 'hint', 'voice', 'reasoningHint'].map((feld) => `ai.providers.realtime.wege.${weg}.${feld}`)),
  'ai.reasoning.levels.low',
  'ai.reasoning.levels.medium',
  'ai.reasoning.levels.high',
]

// Die beiden Panelsprachen — seit 09/2026 gibt es keine weiteren. Die neun
// Teilübersetzungen sind gefallen, weil sie über die Spracherkennung des
// Browsers aktiv wurden, ohne je vollständig gewesen zu sein.
const SPRACHEN: Record<string, unknown> = { de, en }

function blatt(baum: unknown, pfad: string): unknown {
  return pfad.split('.').reduce<unknown>(
    (knoten, teil) =>
      knoten && typeof knoten === 'object'
        ? (knoten as Record<string, unknown>)[teil]
        : undefined,
    baum,
  )
}

describe('abgelöste Übersetzungsschlüssel', () => {
  it.each(Object.keys(SPRACHEN))('%s hat keinen abgelösten Schlüssel mehr', (sprache) => {
    const uebrig = ABGELOESTE_SCHLUESSEL.filter(
      (pfad) => blatt(SPRACHEN[sprache], pfad) !== undefined,
    )
    expect(uebrig).toEqual([])
  })

  it.each(Object.keys(SPRACHEN))('%s kennt alle Nachfolger', (sprache) => {
    const fehlend = NACHFOLGER.filter(
      (pfad) => typeof blatt(SPRACHEN[sprache], pfad) !== 'string',
    )
    expect(fehlend).toEqual([])
  })
})
