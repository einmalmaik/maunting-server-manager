/**
 * Läuft der Browser auf einem Android-Gerät? Die Frage stellt
 * `lib/istAndroid.ts` für beide Apps; hier bleibt der Pfad, den der Browser
 * schon überall importiert. Am Handy steht die Leiste unten
 * (`kopf/HandyLeiste.tsx`), und was Android nicht kann (Entwicklerwerkzeuge,
 * Ton je Tab, Ordnerwahl, Seitenrechte, Löschen ab einem Zeitpunkt), bietet
 * die Oberfläche dort nicht an.
 */
export { istAndroid } from '@/lib/istAndroid'
