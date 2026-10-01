/**
 * Zeigt ein PDF Seite für Seite, gezeichnet mit pdf.js.
 *
 * pdf.js wird erst hier nachgeladen, und zwar der Legacy-Bau: der moderne ruft
 * Funktionen wie `Map.prototype.getOrInsertComputed` und `Math.sumPrecise`, die
 * ein nicht ganz aktuelles Android-WebView (Chrome 124 im Emulator) nicht kennt;
 * der Worker stirbt dort ohne Meldung. Sein Worker liegt im eigenen Bündel
 * (gleiche Herkunft, kein Abruf von außen). Die Bytes gehen direkt an pdf.js,
 * es gibt keine Adresse, die abgerufen würde. Skripte im PDF laufen nie (ohne
 * den Viewer gibt es keine Skript-Sandbox, XFA ist aus), Links und Formulare
 * werden nicht gezeichnet: nur das Bild der Seite.
 *
 * Seiten werden erst gezeichnet, wenn sie in die Nähe des sichtbaren Bereichs
 * kommen; ein Dokument mit 500 Seiten kostet beim Öffnen eine Seite.
 */
import { useEffect, useRef, useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Minus, MoveHorizontal, Plus } from 'lucide-react'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'

const ZOOM_STUFEN = [0.5, 0.75, 1, 1.25, 1.5, 2, 3]
const RAND = 16

let workerGesetzt = false

async function pdfjsLaden() {
  const [pdfjs, { default: workerAdresse }] = await Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url')])
  if (!workerGesetzt) {
    pdfjs.GlobalWorkerOptions.workerPort = new Worker(workerAdresse, { type: 'module' })
    workerGesetzt = true
  }
  return pdfjs
}

export interface PdfAnsichtProps {
  daten: Uint8Array
  label: string
  /** Auf dunklem Grund (Lichtbox): helle Bedienleiste. */
  aufDunkel?: boolean
}

export function PdfAnsicht({ daten, label, aufDunkel = false }: PdfAnsichtProps) {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const [dokument, setDokument] = useState<PDFDocumentProxy | null>(null)
  const [fehler, setFehler] = useState(false)
  const [breite, setBreite] = useState(0)
  const [seitenBreite, setSeitenBreite] = useState(0)
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    let aufgabe: { destroy: () => Promise<void> } | null = null
    let vorbei = false
    setDokument(null)
    setFehler(false)
    void (async () => {
      try {
        const pdfjs = await pdfjsLaden()
        // pdf.js übernimmt den Puffer in den Worker; die Kopie lässt den Aufrufer unberührt.
        const laden = pdfjs.getDocument({ data: daten.slice(), enableXfa: false })
        aufgabe = laden
        const doc = await laden.promise
        const erste = await doc.getPage(1)
        if (vorbei) return
        setSeitenBreite(erste.getViewport({ scale: 1 }).width)
        setDokument(doc)
      } catch {
        if (!vorbei) setFehler(true)
      }
    })()
    return () => {
      vorbei = true
      void aufgabe?.destroy()
    }
  }, [daten])

  useEffect(() => {
    const el = flaeche.current
    if (!el) return
    const messen = () => setBreite(el.clientWidth)
    messen()
    const beobachter = new ResizeObserver(messen)
    beobachter.observe(el)
    return () => beobachter.disconnect()
  }, [])

  // Zoom 1 heißt: eine Seite so breit wie die Fläche, höchstens 900 px.
  const passend = seitenBreite > 0 ? Math.min(breite - 2 * RAND, 900) / seitenBreite : 1
  const massstab = Math.max(passend, 0.1) * zoom
  const knopf = aufDunkel ? 'text-white/85 hover:bg-white/10 hover:text-white' : ''
  const stufe = ZOOM_STUFEN.indexOf(zoom)

  return (
    <div className="flex h-full w-full flex-col">
      <div className={cx('flex shrink-0 items-center justify-center gap-1 py-1.5 text-label-sm', aufDunkel ? 'text-white/70' : 'text-on-surface-variant')}>
        <span className="mr-2 tabular-nums">{dokument ? t('common.pdf.seiten', { count: dokument.numPages }) : ''}</span>
        <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('common.pdf.kleiner')} disabled={stufe <= 0} onClick={() => setZoom(ZOOM_STUFEN[stufe - 1])}>
          <Minus className="h-4 w-4" />
        </Button>
        <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)} %</span>
        <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('common.pdf.groesser')} disabled={stufe >= ZOOM_STUFEN.length - 1} onClick={() => setZoom(ZOOM_STUFEN[stufe + 1])}>
          <Plus className="h-4 w-4" />
        </Button>
        <Button type="button" variant="ghost" size="icon" className={knopf} aria-label={t('common.pdf.breite')} disabled={zoom === 1} onClick={() => setZoom(1)}>
          <MoveHorizontal className="h-4 w-4" />
        </Button>
      </div>
      <div ref={flaeche} role="document" aria-label={label} tabIndex={0} className="min-h-0 flex-1 overflow-auto focus-visible:outline-none">
        {fehler ? (
          <p className={cx('p-6 text-center text-sm', aufDunkel ? 'text-white/70' : 'text-on-surface-variant')}>{t('common.pdf.fehler')}</p>
        ) : !dokument ? (
          <p className={cx('p-6 text-center text-sm', aufDunkel ? 'text-white/70' : 'text-on-surface-variant')}>{t('common.pdf.laedt')}</p>
        ) : (
          <div className="flex w-max min-w-full flex-col items-center gap-3 py-3" style={{ paddingInline: RAND }}>
            {Array.from({ length: dokument.numPages }, (_, i) => (
              <PdfSeite
                key={i}
                dokument={dokument}
                flaeche={flaeche}
                nummer={i + 1}
                massstab={massstab}
                // Bis die Seite gemessen ist: so breit wie die erste, im A4-Verhältnis.
                vorgabe={{ b: seitenBreite * massstab, h: seitenBreite * massstab * Math.SQRT2 }}
                label={t('common.pdf.seite', { nummer: i + 1 })}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

interface PdfSeiteProps {
  dokument: PDFDocumentProxy
  /** Die Scrollfläche: nur an ihr wirkt der Vorlauf, am Fenster schneidet sie ihn ab. */
  flaeche: RefObject<HTMLDivElement>
  nummer: number
  massstab: number
  vorgabe: { b: number; h: number }
  label: string
}

function PdfSeite({ dokument, flaeche, nummer, massstab, vorgabe, label }: PdfSeiteProps) {
  const huelle = useRef<HTMLDivElement>(null)
  const leinwand = useRef<HTMLCanvasElement>(null)
  const [groesse, setGroesse] = useState<{ b: number; h: number } | null>(null)
  const [sichtbar, setSichtbar] = useState(false)

  useEffect(() => {
    const el = huelle.current
    if (!el) return
    const beobachter = new IntersectionObserver(([e]) => setSichtbar(e.isIntersecting), { root: flaeche.current, rootMargin: '800px 0px' })
    beobachter.observe(el)
    return () => beobachter.disconnect()
  }, [flaeche])

  useEffect(() => {
    let vorbei = false
    let aufgabe: { cancel: () => void; promise: Promise<void> } | null = null
    void (async () => {
      const seite = await dokument.getPage(nummer)
      const vp = seite.getViewport({ scale: massstab })
      if (vorbei) return
      setGroesse({ b: vp.width, h: vp.height })
      const canvas = leinwand.current
      if (!sichtbar || !canvas) return
      const dichte = window.devicePixelRatio || 1
      canvas.width = Math.floor(vp.width * dichte)
      canvas.height = Math.floor(vp.height * dichte)
      aufgabe = seite.render({ canvas, viewport: vp, transform: dichte === 1 ? undefined : [dichte, 0, 0, dichte, 0, 0] })
      await aufgabe.promise.catch(() => {})
    })().catch(() => {})
    return () => {
      vorbei = true
      aufgabe?.cancel()
    }
  }, [dokument, nummer, massstab, sichtbar])

  return (
    <div
      ref={huelle}
      role="img"
      aria-label={label}
      className="shrink-0 bg-white shadow-lg"
      style={{ width: (groesse ?? vorgabe).b, height: (groesse ?? vorgabe).h }}
    >
      {sichtbar && <canvas ref={leinwand} className="block h-full w-full" />}
    </div>
  )
}
