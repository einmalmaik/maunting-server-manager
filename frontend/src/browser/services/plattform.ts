/**
 * Läuft der Browser auf einem Android-Gerät? Eine Stelle für die Frage statt
 * Prüfungen des User-Agents über den Code verteilt. Am Handy steht die
 * Leiste unten (`kopf/HandyLeiste.tsx`), und was Android nicht kann
 * (Entwicklerwerkzeuge, Ton je Tab, Ordnerwahl, Seitenrechte, Löschen ab
 * einem Zeitpunkt), bietet die Oberfläche dort nicht an.
 */
export function istAndroid(): boolean {
  return /\bAndroid\b/.test(navigator.userAgent)
}
