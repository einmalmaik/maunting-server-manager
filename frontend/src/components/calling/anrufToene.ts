/**
 * Die kurzen Signale des Anrufs: jemand kommt, jemand geht, aufgelegt.
 *
 * Erzeugt statt abgespielt. Eine Audiodatei müsste ausgeliefert, zwischenge-
 * speichert und in der Desktop-App über ein zweites Protokoll geladen werden;
 * zwei Oszillatoren tun dasselbe in wenigen Zeilen und klingen überall gleich.
 *
 * Alles hier ist beiläufig: scheitert ein Ton, passiert nichts weiter. Ein
 * Signalton darf nie der Grund sein, warum ein Anruf hakt.
 */

type AudioContextKlasse = typeof AudioContext

function kontextKlasse(): AudioContextKlasse | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { AudioContext?: AudioContextKlasse; webkitAudioContext?: AudioContextKlasse }
  return w.AudioContext ?? w.webkitAudioContext ?? null
}

let kontext: AudioContext | null = null

function hole(): AudioContext | null {
  if (kontext) return kontext
  const Klasse = kontextKlasse()
  if (!Klasse) return null
  try {
    kontext = new Klasse()
  } catch {
    return null
  }
  return kontext
}

/**
 * Ein Ton mit weichen Flanken. Ohne die Rampen klickt es an den Kanten hörbar,
 * und ein Klicken klingt nach Defekt, nicht nach Signal.
 */
function ton(frequenz: number, startVersatz: number, dauer: number, lautstaerke: number): void {
  const ctx = hole()
  if (!ctx) return
  const start = ctx.currentTime + startVersatz
  const oszillator = ctx.createOscillator()
  const huelle = ctx.createGain()
  oszillator.type = 'sine'
  oszillator.frequency.value = frequenz
  huelle.gain.setValueAtTime(0.0001, start)
  huelle.gain.exponentialRampToValueAtTime(lautstaerke, start + 0.015)
  huelle.gain.exponentialRampToValueAtTime(0.0001, start + dauer)
  oszillator.connect(huelle).connect(ctx.destination)
  oszillator.start(start)
  oszillator.stop(start + dauer + 0.02)
}

function spiele(folge: Array<[frequenz: number, versatz: number, dauer: number]>, lautstaerke: number): void {
  const ctx = hole()
  if (!ctx) return
  // Ohne vorherige Nutzergeste ist der Kontext angehalten. Dann bleibt es still,
  // statt den Ton in eine Warteschlange zu legen, die irgendwann losgeht.
  if (ctx.state === 'suspended') {
    void ctx.resume().catch(() => {})
  }
  folge.forEach(([frequenz, versatz, dauer]) => ton(frequenz, versatz, dauer, lautstaerke))
}

/** Aufsteigend: jemand ist dazugekommen. */
export function toneBeitritt(): void {
  spiele([[523.25, 0, 0.1], [783.99, 0.1, 0.14]], 0.09)
}

/** Absteigend, dieselben Töne rückwärts: jemand ist gegangen. */
export function toneAbgang(): void {
  spiele([[783.99, 0, 0.1], [523.25, 0.1, 0.14]], 0.09)
}

/** Tief und kurz doppelt: das Gespräch ist zu Ende. */
export function toneAufgelegt(): void {
  spiele([[392, 0, 0.12], [261.63, 0.13, 0.2]], 0.1)
}

/** Angenehmer Doppelton: Anruf geräteübergreifend übertragen oder übernommen (wie bei Discord). */
export function toneUebergabe(): void {
  spiele([[440, 0, 0.08], [659.25, 0.08, 0.12]], 0.09)
}

/** Nur für Tests: den geteilten Kontext vergessen. */
export function setzeToeneZurueck(): void {
  void kontext?.close().catch(() => {})
  kontext = null
}
