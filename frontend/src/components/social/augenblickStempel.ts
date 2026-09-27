/**
 * Uhrzeit und Datum auf einem Augenblick — ins Bild gezeichnet, nicht darübergelegt.
 *
 * Der Stempel landet in den Pixeln, bevor das Foto verschlüsselt wird. Die
 * Gegenseite braucht dafür weder die Schriften noch diesen Code, und der
 * Stempel zeigt überall dasselbe: die Uhrzeit des Auslösens auf dem Gerät,
 * das ausgelöst hat.
 *
 * Alle Stile stehen waagrecht in der Mitte und zwischen 35 und 75 Prozent der
 * Höhe: oben liegt der Titel über dem Bild, unten die Knöpfe. Der Sucher
 * schneidet ausserdem per `object-cover` zu — ein Querformatfoto am
 * hochkant gehaltenen Telefon zeigt nur ein Viertel seiner Breite. Deshalb
 * misst sich der Stempel an diesem sichtbaren Ausschnitt (`sichtSeitenverhaeltnis`),
 * nicht am ganzen Foto: sonst wäre er in der Vorschau angeschnitten.
 */

export const STEMPEL_STILE = ['ohne', 'gross', 'handschrift', 'elegant', 'digital', 'schlicht'] as const
export type StempelStil = (typeof STEMPEL_STILE)[number]

/** Die Texte eines Stempels, in der Sprache der Oberfläche. */
export interface StempelTexte {
  zeit: string
  wochentag: string
  datumLang: string
  datumMitJahr: string
  datumKurz: string
  kurz: string
}

export function stempelTexte(zeitpunkt: Date, sprache: string): StempelTexte {
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(sprache, o).format(zeitpunkt)
  return {
    zeit: f({ hour: '2-digit', minute: '2-digit' }),
    wochentag: f({ weekday: 'long' }),
    datumLang: f({ day: 'numeric', month: 'long' }),
    datumMitJahr: f({ day: 'numeric', month: 'long', year: 'numeric' }),
    datumKurz: f({ day: '2-digit', month: '2-digit', year: '2-digit' }),
    kurz: `${f({ hour: '2-digit', minute: '2-digit' })} · ${f({ weekday: 'short', day: 'numeric', month: 'short' })}`,
  }
}

let schriften: Promise<void> | null = null

/**
 * Die Stempelschriften, erst beim ersten Stempel. Wer nie einen setzt, lädt
 * sie nie. Ein `document.fonts.load` danach, weil ein Canvas nicht auf die
 * Schrift wartet: ohne ihn zeichnete der erste Stempel in der Ersatzschrift.
 */
export function ladeStempelSchriften(): Promise<void> {
  if (!schriften) {
    schriften = Promise.all([
      import('@fontsource/bebas-neue/latin-400.css'),
      import('@fontsource/caveat/latin-700.css'),
      import('@fontsource/playfair-display/latin-400.css'),
      import('@fontsource/playfair-display/latin-400-italic.css'),
      import('@fontsource/orbitron/latin-700.css'),
    ])
      .then(() =>
        Promise.all([
          document.fonts.load('400 64px "Bebas Neue"'),
          document.fonts.load('700 64px "Caveat"'),
          document.fonts.load('400 64px "Playfair Display"'),
          document.fonts.load('italic 400 64px "Playfair Display"'),
          document.fonts.load('700 64px "Orbitron"'),
          document.fonts.load('600 64px "Manrope"'),
        ]),
      )
      .then(() => undefined)
      .catch(() => {
        // Beim nächsten Stempel noch einmal versuchen; dieser zeichnet mit
        // der Ersatzschrift, statt auszufallen.
        schriften = null
      })
  }
  return schriften
}

/** Setzt die Schrift und verkleinert sie, bis der Text in `maxBreite` passt. */
function passend(ctx: CanvasRenderingContext2D, text: string, schrift: (px: number) => string, px: number, maxBreite: number): number {
  ctx.font = schrift(px)
  const breite = ctx.measureText(text).width
  if (breite <= maxBreite) return px
  const kleiner = Math.floor((px * maxBreite) / breite)
  ctx.font = schrift(kleiner)
  return kleiner
}

function sperren(ctx: CanvasRenderingContext2D, px: number) {
  // Nicht jede WebView kennt letterSpacing am Canvas; ohne sieht es enger aus, bleibt aber richtig.
  if ('letterSpacing' in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${px}px`
}

function schatten(ctx: CanvasRenderingContext2D, s: number, farbe = 'rgba(0,0,0,0.45)') {
  ctx.shadowColor = farbe
  ctx.shadowBlur = s * 0.025
  ctx.shadowOffsetY = s * 0.004
}

function pille(ctx: CanvasRenderingContext2D, x: number, y: number, b: number, h: number) {
  const r = h / 2
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + b, y, x + b, y + h, r)
  ctx.arcTo(x + b, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + b, y, r)
  ctx.closePath()
}

/** Zeichnet den Stempel auf ein Canvas, das das Foto schon trägt. */
export function zeichneStempel(
  ctx: CanvasRenderingContext2D,
  b: number,
  h: number,
  stil: StempelStil,
  texte: StempelTexte,
  sichtSeitenverhaeltnis = b / h,
) {
  if (stil === 'ohne') return
  // Breite des Teils, den der Sucher zeigt. Schneidet er oben und unten, bleibt die volle Breite.
  const sichtBreite = Math.min(b, h * sichtSeitenverhaeltnis)
  const s = Math.min(sichtBreite, h)
  const mitte = b / 2
  const max = sichtBreite * 0.86
  ctx.save()
  ctx.textAlign = 'center'
  ctx.textBaseline = 'alphabetic'
  ctx.fillStyle = '#ffffff'

  if (stil === 'gross') {
    const y = h * 0.5
    schatten(ctx, s)
    const px = passend(ctx, texte.zeit, (p) => `400 ${p}px "Bebas Neue", sans-serif`, s * 0.36, max)
    ctx.fillText(texte.zeit, mitte, y + px * 0.3)
    const unter = `${texte.wochentag}, ${texte.datumLang}`.toUpperCase()
    sperren(ctx, s * 0.01)
    passend(ctx, unter, (p) => `600 ${p}px Manrope, sans-serif`, s * 0.042, max)
    ctx.fillText(unter, mitte, y + px * 0.3 + s * 0.075)
  } else if (stil === 'handschrift') {
    ctx.translate(mitte, h * 0.64)
    ctx.rotate((-4 * Math.PI) / 180)
    schatten(ctx, s)
    const oben = `${texte.wochentag}, ${texte.zeit}`
    const px = passend(ctx, oben, (p) => `700 ${p}px Caveat, cursive`, s * 0.13, max)
    ctx.fillText(oben, 0, 0)
    passend(ctx, texte.datumLang, (p) => `700 ${p}px Caveat, cursive`, px * 0.62, max)
    ctx.fillText(texte.datumLang, 0, px * 0.8)
  } else if (stil === 'elegant') {
    const y = h * 0.42
    schatten(ctx, s, 'rgba(0,0,0,0.35)')
    const px = passend(ctx, texte.zeit, (p) => `italic 400 ${p}px "Playfair Display", serif`, s * 0.22, max)
    ctx.fillText(texte.zeit, mitte, y + px * 0.35)
    const unter = texte.datumMitJahr.toUpperCase()
    // Zwei feine Linien links und rechts vom Datum; ihr Platz geht vom Datum ab.
    const linie = s * 0.06
    const luecke = s * 0.025
    sperren(ctx, s * 0.012)
    passend(ctx, unter, (p) => `400 ${p}px "Playfair Display", serif`, s * 0.04, max - 2 * (linie + luecke))
    ctx.fillText(unter, mitte, y + px * 0.35 + s * 0.07)
    const breite = ctx.measureText(unter).width
    ctx.shadowColor = 'transparent'
    ctx.fillRect(mitte - breite / 2 - linie - luecke, y + px * 0.35 + s * 0.058, linie, Math.max(1, s * 0.003))
    ctx.fillRect(mitte + breite / 2 + luecke, y + px * 0.35 + s * 0.058, linie, Math.max(1, s * 0.003))
  } else if (stil === 'digital') {
    const y = h * 0.6
    const px = passend(ctx, texte.zeit, (p) => `700 ${p}px Orbitron, monospace`, s * 0.12, max * 0.8)
    const zeitBreite = ctx.measureText(texte.zeit).width
    const pb = zeitBreite + s * 0.12
    const ph = px + s * 0.12
    ctx.fillStyle = 'rgba(0,0,0,0.5)'
    pille(ctx, mitte - pb / 2, y - ph / 2, pb, ph)
    ctx.fill()
    ctx.fillStyle = '#b8fbff'
    ctx.shadowColor = 'rgba(80,240,255,0.9)'
    ctx.shadowBlur = s * 0.03
    ctx.fillText(texte.zeit, mitte, y + px * 0.2)
    ctx.shadowBlur = s * 0.015
    sperren(ctx, s * 0.008)
    ctx.font = `700 ${Math.round(px * 0.26)}px Orbitron, monospace`
    ctx.fillText(texte.datumKurz, mitte, y + px * 0.2 + px * 0.42)
  } else if (stil === 'schlicht') {
    const y = h * 0.72
    const px = passend(ctx, texte.kurz, (p) => `600 ${p}px Manrope, sans-serif`, s * 0.048, max * 0.85)
    const tb = ctx.measureText(texte.kurz).width
    const pb = tb + px * 1.6
    const ph = px * 2.1
    ctx.shadowColor = 'rgba(0,0,0,0.25)'
    ctx.shadowBlur = s * 0.02
    ctx.fillStyle = 'rgba(255,255,255,0.92)'
    pille(ctx, mitte - pb / 2, y - ph / 2, pb, ph)
    ctx.fill()
    ctx.shadowColor = 'transparent'
    ctx.fillStyle = '#111418'
    ctx.textBaseline = 'middle'
    ctx.fillText(texte.kurz, mitte, y + px * 0.04)
  }
  ctx.restore()
}

/** Das Foto mit Stempel als neues JPEG. `ohne` gibt das Foto unverändert zurück. */
export async function stempleFoto(
  dataUrl: string,
  stil: StempelStil,
  texte: StempelTexte,
  sichtSeitenverhaeltnis?: number,
): Promise<string> {
  if (stil === 'ohne') return dataUrl
  await ladeStempelSchriften()
  const bild = new Image()
  bild.src = dataUrl
  await bild.decode()
  const canvas = document.createElement('canvas')
  canvas.width = bild.naturalWidth
  canvas.height = bild.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) return dataUrl
  ctx.drawImage(bild, 0, 0)
  zeichneStempel(ctx, canvas.width, canvas.height, stil, texte, sichtSeitenverhaeltnis)
  return canvas.toDataURL('image/jpeg', 0.88)
}
