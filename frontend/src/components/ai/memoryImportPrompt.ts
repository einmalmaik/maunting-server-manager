/**
 * Der Auszugs-Prompt der Memory Bridge — den schickt der Benutzer an seine
 * bisherige KI, deren Antwort fügt er im Import wieder ein.
 *
 * Er steht hier und nicht in der Sprachdatei: er ist ein Text **an eine
 * Maschine**, dessen Form der Parser in `backend/services/ai_memory_import_service.py`
 * wiedererkennen muss — die fünf Abschnitte, die Unterpunkte „Beleg:“ und die
 * Schlusszeile „Importiert aus:“. Wer eine Überschrift hier ändert, prüft dort
 * `KATEGORIEN` und `_UEBERSCHRIFT_ANFANG`.
 *
 * Zwei Fassungen, weil die fremde KI in der Sprache des Prompts antwortet und
 * der Parser beide Sprachen liest.
 */
const DEUTSCH = `Hilf mir, Kontext von einem KI-Assistenten in einen anderen zu importieren. Deine Aufgabe ist es, unsere bisherigen Unterhaltungen durchzugehen und zusammenzufassen, was du über mich weißt.

Vermeide in der Ausgabe Pronomen der ersten Person (ich, mich, mir, mein) und der zweiten Person (du, dich, dir, dein). Bezeichne die Person, über die du Informationen erhalten hast, stattdessen als „die Person“ oder verwende eine andere neutrale Formulierung.

Behalte den eingegebenen Wortlaut nach Möglichkeit bei, insbesondere bei Anweisungen und Vorlieben.

Kategorien (Ausgabe in dieser Reihenfolge):
1. Demografische Informationen: bevorzugte Namen, Beruf, Bildungsstand und Wohnort.
2. Interessen und Vorlieben: Dinge, mit denen ich mich aktiv und regelmäßig beschäftige (nicht nur einmalige Käufe oder Besitztümer).
3. Soziales Umfeld: bestätigte, langfristige Kontakte.
4. Termine, Projekte und Pläne mit Datum: ein Protokoll bedeutender Aktivitäten der letzten Zeit.
5. Anweisungen: Regeln, um deren Einhaltung ich dich explizit gebeten habe – „Mach immer X“, „Mach niemals Y“ sowie Korrekturen an deinem Verhalten. Berücksichtige nur Regeln aus gemerkten Informationen, nicht aus Unterhaltungen.

Format:
Teile die Inhalte mithilfe der oben genannten Kategorien in die entsprechenden Abschnitte auf. Verwende nach Möglichkeit wörtliche Zitate aus meinen Prompts, um die einzelnen Einträge zu belegen. Verwende für jeden Eintrag das folgende Format:
* Der Name der Person ist <Name>.
    * Beleg: Die Person hat gesagt: „Nenn mich <Name>“. Datum: [TT.MM.JJJJ].

Ausgabe:
– Gib NUR die angeforderten Informationen aus. Verwende keine Füllwörter, Einleitungen oder Verabschiedungen.

Vervollständige zum Schluss den Satz „Importiert aus: <Name>“. Das Wort „Name“ muss durch „ChatGPT“, „Claude“, „Grok“ usw. ersetzt werden. Dies muss der allerletzte Text in deiner Antwort sein.`

const ENGLISCH = `Help me import context from one AI assistant into another. Your task is to go through our past conversations and summarize what you know about me.

Avoid first-person pronouns (I, me, my) and second-person pronouns (you, your) in the output. Refer to the person you learned about as "the person" or use another neutral phrasing instead.

Keep the original wording wherever possible, especially for instructions and preferences.

Categories (output in this order):
1. Demographic information: preferred names, occupation, education and location.
2. Interests and preferences: things I actively and regularly engage with (not one-off purchases or possessions).
3. Relationships: confirmed, long-term contacts.
4. Dated events, projects and plans: a log of significant recent activities.
5. Instructions: rules I explicitly asked you to follow – "Always do X", "Never do Y" and corrections to your behavior. Only include rules from saved memories, not from conversations.

Format:
Split the content into sections using the categories above. Where possible, quote my prompts verbatim to support each entry. Use this format for every entry:
* The person's name is <Name>.
    * Evidence: The person said: "Call me <Name>". Date: [DD.MM.YYYY].

Output:
– Output ONLY the requested information. No filler, introductions or sign-offs.

Finally, complete the sentence "Imported from: <Name>". Replace "Name" with "ChatGPT", "Claude", "Grok" etc. This must be the very last text of your answer.`

export function memoryImportPrompt(language: string): string {
  return language.toLowerCase().startsWith('de') ? DEUTSCH : ENGLISCH
}
