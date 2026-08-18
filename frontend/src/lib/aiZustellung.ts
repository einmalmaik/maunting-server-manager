/**
 * Das Zustell-Ereignis der KI: „im Dauerchat ist etwas angekommen".
 *
 * Die Glocke (`AiRunNotice`) ist der einzige Ort, der auch bei geschlossenem
 * Chat pollt — sie feuert dieses Ereignis, wenn ein Lauf endet oder ein
 * Hintergrund-Auftrag seinen Zustand wechselt. Der offene Chat und die
 * Worker-Ansicht laden daraufhin ihren Verlauf nach, statt selbst zu pollen.
 *
 * Dieselbe Bauart wie `SUPPORT_WIDGET_UPDATED_EVENT` (`supportWidgetLoader.ts`)
 * und bewusst nur ein Signal ohne Nutzlast: Broker und Meldestelle sind
 * prozesslokal, das Frontend darf sich nie auf ein Ereignis als einzige
 * Wahrheit verlassen — nachgeladen wird immer aus der Datenbank.
 */
export const AI_ZUSTELLUNG_EVENT = 'msm:ai-zustellung'

export function zustellungMelden(): void {
  window.dispatchEvent(new CustomEvent(AI_ZUSTELLUNG_EVENT))
}
