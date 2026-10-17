/**
 * Automatischer Link-Reiniger (Clean Copy).
 *
 * Entfernt beim Kopieren von URLs automatisch Klick-, Kampagnen- und
 * Tracking-Parameter (z. B. utm_*, fbclid, si), während legitime funktionale
 * Parameter (z. B. Video-IDs wie v=..., Zeitstempel t=..., Suchbegriffe q=...
 * und Partnerkennungen wie tag=...) vollständig erhalten bleiben.
 */
import { toast } from '@/stores/toastStore'
import { useEinstellungenStore } from './einstellungenStore'

/**
 * Bekannte Tracking- und Klick-Kennungen.
 * Identisch mit der Rust-Filterliste in `schild/verfolgung.rs`.
 */
const TRACKING_NAMEN: ReadonlySet<string> = new Set([
  'fbclid',
  'gclid',
  'gclsrc',
  'dclid',
  'gbraid',
  'wbraid',
  'msclkid',
  'yclid',
  'ysclid',
  'twclid',
  'ttclid',
  'igshid',
  'igsh',
  'li_fat_id',
  'mc_cid',
  'mc_eid',
  '_hsenc',
  '_hsmi',
  '__hssc',
  '__hstc',
  '__hsfp',
  'hsctatracking',
  'mkt_tok',
  'oly_anon_id',
  'oly_enc_id',
  'vero_id',
  'vero_conv',
  'wickedid',
  'rb_clickid',
  '_openstat',
  '__s',
  'srsltid',
  'si',
  'feature',
  'ref_src',
  'ref_url',
  'ved',
  'usg',
  'cvid',
  'pd_rd_r',
  'pd_rd_w',
  'pd_rd_wg',
  'pf_rd_p',
  'pf_rd_r',
  'pf_rd_m',
  'pf_rd_s',
  'pf_rd_t',
  'pf_rd_i',
])

/**
 * Prüft, ob ein Parametername eine bekannte Tracking-Kennung darstellt.
 */
export function istTrackingParameter(name: string): boolean {
  const klein = name.toLowerCase()
  return klein.startsWith('utm_') || TRACKING_NAMEN.has(klein)
}

export interface BereinigungsErgebnis {
  url: string
  bereinigt: boolean
}

/**
 * Bereinigt eine URL von allen bekannten Tracking-Parametern.
 *
 * Gibt `{ url, bereinigt: true }` zurück, wenn Parameter entfernt wurden,
 * sonst den Originaltext mit `{ url, bereinigt: false }`.
 */
export function bereinigeUrl(urlText: string): BereinigungsErgebnis {
  const eingabe = urlText.trim()
  if (!eingabe || (!eingabe.startsWith('http://') && !eingabe.startsWith('https://'))) {
    return { url: urlText, bereinigt: false }
  }

  try {
    const parsed = new URL(eingabe)
    const zuEntfernen: string[] = []

    for (const [key] of parsed.searchParams) {
      if (istTrackingParameter(key)) {
        zuEntfernen.push(key)
      }
    }

    if (zuEntfernen.length === 0) {
      return { url: urlText, bereinigt: false }
    }

    for (const key of zuEntfernen) {
      parsed.searchParams.delete(key)
    }

    // Wenn keine Suchparameter mehr übrig sind, saubere URL ohne Fragezeichen
    const search = parsed.searchParams.toString()
    let neu = `${parsed.origin}${parsed.pathname}`
    if (search) {
      neu += `?${search}`
    }
    if (parsed.hash) {
      neu += parsed.hash
    }

    return { url: neu, bereinigt: true }
  } catch {
    return { url: urlText, bereinigt: false }
  }
}

/**
 * Kopiert einen Link in die Zwischenablage und bereinigt ihn automatisch,
 * sofern Clean Copy in den Einstellungen aktiv ist.
 *
 * Bei erfolgreicher Bereinigung kann optional ein Toast-Hinweis eingeblendet werden.
 */
export async function kopiereSauberenLink(text: string, hinweis?: string): Promise<boolean> {
  const cleanAktiv = useEinstellungenStore.getState().cleanCopy
  let zuKopieren = text
  let wurdeBereinigt = false

  if (cleanAktiv) {
    const ergebnis = bereinigeUrl(text)
    zuKopieren = ergebnis.url
    wurdeBereinigt = ergebnis.bereinigt
  }

  try {
    await navigator.clipboard.writeText(zuKopieren)
    if (wurdeBereinigt && hinweis) {
      toast.success(hinweis)
    }
    return true
  } catch {
    return false
  }
}
