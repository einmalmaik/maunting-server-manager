/**
 * Aus einer gebuchten USD-Zahl ein Betrag, den der Betreiber lesen kann.
 *
 * Gebucht wird ausnahmslos in **US-Cent-Microunits** (1 Cent = 10.000). Der
 * Grund steht im Backend (`services/ai_kosten.py`): OpenRouter meldet die
 * tatsächlich belasteten Kosten in USD, und eine Umrechnung *vor* der Buchung
 * wäre eine zweite Fehlerquelle in genau der Zahl, die stimmen soll. Ein Kurs
 * ändert sich täglich; eine gebuchte Zeile darf sich nicht ändern.
 *
 * Umgerechnet wird deshalb genau hier — an einer einzigen Stelle, ganz am Rand.
 * Wer den Kurs anderswo anwendet, hat ihn zweimal angewandt.
 */

export interface Kostenpolitik {
  currency: string
  /** Was ein US-Dollar in der Anzeigewährung wert ist. Als Zeichenkette, weil
   *  ein Kurs eine Dezimalzahl ist und der Umweg über die Schnittstelle sie
   *  nicht verlieren soll. */
  usd_rate: string
}

export interface Betrag {
  /** In der Anzeigewährung, z. B. „1,84 €". */
  primaer: string
  /** Derselbe Betrag in USD — der Währung, in der abgerechnet wurde. `null`,
   *  wenn die Anzeigewährung ohnehin USD ist: „2,00 $ (2,00 $)" hilft niemandem. */
  sekundaer: string | null
}

const MICRO_JE_EINHEIT = 1_000_000

/** USD, die Währung der Buchung — der Rückfall, wenn nichts anderes feststeht. */
const OHNE_POLITIK: Kostenpolitik = { currency: 'USD', usd_rate: '1' }

function politikAus(politik: Kostenpolitik | null | undefined): Kostenpolitik {
  // Eine fehlende Politik darf keine Ansicht umwerfen. Dieselbe Haltung wie im
  // Backend (`services/ai_kosten.py`): beim Lesen gilt im Zweifel die Vorgabe.
  // Sie kostete hier ein `t.usd_rate of undefined` in einer Karte, deren
  // ganzer Zweck es ist, einem abgewiesenen Benutzer zu erklären, woran es lag.
  if (!politik || typeof politik.currency !== 'string') return OHNE_POLITIK
  return politik
}

function kursAus(politik: Kostenpolitik): number {
  if (politik.currency === 'USD') return 1
  const wert = Number.parseFloat(politik.usd_rate)
  // Ein unlesbarer oder unsinniger Kurs darf keine Kostenanzeige umwerfen. 1
  // ist dabei der ehrlichste Rückfall: er zeigt den Betrag so, wie er gebucht
  // wurde, statt ihn mit einer erfundenen Zahl zu multiplizieren.
  return Number.isFinite(wert) && wert > 0 ? wert : 1
}

function formatieren(betrag: number, waehrung: string, sprache: string): string {
  try {
    return new Intl.NumberFormat(sprache, {
      style: 'currency',
      currency: waehrung,
      // Zwei Nachkommastellen sind zu grob: die meisten Einzelanfragen kosten
      // weniger als einen Cent und stünden alle als „0,00 €" da — genau die
      // Ansicht, mit der sich eine Rechnung nicht prüfen lässt. Vier Stellen
      // zeigen den Unterschied und bleiben lesbar. Bei Beträgen ab einem Euro
      // fallen sie weg, weil dort niemand den Zehntelcent liest.
      minimumFractionDigits: 2,
      maximumFractionDigits: Math.abs(betrag) >= 1 ? 2 : 4,
    }).format(betrag)
  } catch {
    // Eine Währung, die diese Laufzeit nicht kennt, soll die Zahl nicht
    // verschlucken.
    return `${betrag.toFixed(2)} ${waehrung}`
  }
}

/**
 * Formatiert einen gebuchten Betrag in der Anzeigewährung — und daneben in USD.
 */
export function betragFormatieren(
  microUsd: number,
  politik: Kostenpolitik | null | undefined,
  sprache: string,
): Betrag {
  const gilt = politikAus(politik)
  const usd = (Number.isFinite(microUsd) ? microUsd : 0) / MICRO_JE_EINHEIT
  return {
    primaer: formatieren(usd * kursAus(gilt), gilt.currency, sprache),
    sekundaer: gilt.currency === 'USD' ? null : formatieren(usd, 'USD', sprache),
  }
}

/**
 * Ein **Preis**, nicht ein Betrag — und deshalb immer mit vier Nachkommastellen.
 *
 * `betragFormatieren` kürzt ab einem Euro auf zwei Stellen, weil dort niemand
 * den Zehntelcent liest. Bei einem Preis je Million Tokens ist das anders: er
 * wird mit sechsstelligen Zahlen multipliziert, und was hier gerundet aussieht,
 * ist in der Rechnung ein spürbarer Unterschied. Ein Feld, das „1,30 $" zeigt,
 * aber 1,3043 speichert, wäre genau die Sorte stiller Abweichung, gegen die
 * dieser ganze Umbau geht.
 */
export function preisFormatieren(
  microUsd: number,
  waehrung: string,
  sprache: string,
): string {
  const wert = microUsd / MICRO_JE_EINHEIT
  try {
    return new Intl.NumberFormat(sprache, {
      style: 'currency',
      currency: waehrung,
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    }).format(wert)
  } catch {
    return `${wert.toFixed(4)} ${waehrung}`
  }
}

/**
 * Rechnet eine Eingabe in der Anzeigewährung zurück in Microunits.
 *
 * Für das Preisfeld beim Anbieter: der Betreiber tippt „1,20", gespeichert wird
 * die USD-Zahl. Komma und Punkt gelten beide als Dezimaltrennzeichen — welches
 * jemand tippt, hängt an seiner Tastatur und nicht an seiner Absicht.
 *
 * Gerundet wird **auf**, aus demselben Grund wie überall sonst bei Kosten: ein
 * zu niedriger Preis lässt jemanden glauben, er habe noch Luft.
 *
 * `null` heißt „keine Eingabe" und ist etwas anderes als 0 — 0 wäre die Zusage,
 * dass dieses Modell nichts kostet.
 */
export function eingabeInMicroUsd(
  eingabe: string,
  politik: Kostenpolitik | null | undefined,
): number | null {
  const roh = eingabe.trim().replace(',', '.')
  if (!roh) return null
  const wert = Number.parseFloat(roh)
  if (!Number.isFinite(wert) || wert < 0) return null
  return Math.ceil((wert / kursAus(politikAus(politik))) * MICRO_JE_EINHEIT)
}

/**
 * Der Gegenweg: aus gespeicherten Microunits die Zahl fürs Eingabefeld.
 *
 * Ohne Währungszeichen und ohne Tausendertrennung — das hier geht in ein
 * `<input>`, und ein formatierter Betrag wäre dort beim nächsten Speichern
 * nicht mehr lesbar.
 */
export function microUsdInEingabe(
  microUsd: number | null,
  politik: Kostenpolitik | null | undefined,
): string {
  if (microUsd === null || microUsd === undefined) return ''
  const wert = (microUsd / MICRO_JE_EINHEIT) * kursAus(politikAus(politik))
  // Nachkommastellen nur, soweit sie etwas tragen: „3" statt „3.0000".
  return String(Number(wert.toFixed(4)))
}
