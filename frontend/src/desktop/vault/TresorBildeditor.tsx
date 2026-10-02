/**
 * Bild drehen und zuschneiden, auf dem Gerät.
 *
 * Die Vorschau arbeitet mit dem Bild, das gerade angezeigt wird; gespeichert
 * wird aus dem Original in voller Auflösung. Das Ergebnis geht als neue
 * Fassung über `dateiErsetzen`, die alte bleibt unter „frühere Fassungen“.
 * Was der Browser nicht kodieren kann (GIF, HEIC), lässt sich nicht bearbeiten.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCcw, RotateCw, Undo2 } from 'lucide-react'
import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { useVaultStore, type VaultItem } from './vaultStore'
import { blobLesen } from './tresorDateien'

/** Formate, die der Browser auch schreiben kann. */
export const BEARBEITBAR = ['image/jpeg', 'image/png', 'image/webp']

/** Mehr Pixel verkraftet eine Leinwand im WebView nicht überall. */
const HOECHSTENS_PIXEL = 40_000_000

export interface Rahmen {
  x: number
  y: number
  b: number
  h: number
}

const GANZ: Rahmen = { x: 0, y: 0, b: 1, h: 1 }
const MINDESTENS = 0.05

export type Griff = 'nw' | 'ne' | 'sw' | 'se' | 'mitte'

/**
 * Der Zuschnitt nach einem Zug an `griff` um `dx`/`dy` (Anteile der Fläche).
 * Er bleibt im Bild und wird nie kleiner als `MINDESTENS`.
 */
export function rahmenZiehen(s: Rahmen, griff: Griff, dx: number, dy: number): Rahmen {
  let { x, y, b, h } = s
  if (griff === 'mitte') {
    x = Math.min(1 - b, Math.max(0, s.x + dx))
    y = Math.min(1 - h, Math.max(0, s.y + dy))
    return { x, y, b, h }
  }
  if (griff.includes('w')) {
    x = Math.min(s.x + s.b - MINDESTENS, Math.max(0, s.x + dx))
    b = s.x + s.b - x
  } else {
    b = Math.min(1 - s.x, Math.max(MINDESTENS, s.b + dx))
  }
  if (griff.includes('n')) {
    y = Math.min(s.y + s.h - MINDESTENS, Math.max(0, s.y + dy))
    h = s.y + s.h - y
  } else {
    h = Math.min(1 - s.y, Math.max(MINDESTENS, s.h + dy))
  }
  return { x, y, b, h }
}

/** Zeichnet `quelle` um `drehung` Grad gedreht und auf `rahmen` zugeschnitten. */
export function zeichnen(quelle: CanvasImageSource, breite: number, hoehe: number, drehung: number, rahmen: Rahmen, massstab = 1): HTMLCanvasElement {
  const quer = drehung % 180 !== 0
  const gb = quer ? hoehe : breite
  const gh = quer ? breite : hoehe
  const leinwand = document.createElement('canvas')
  leinwand.width = Math.max(1, Math.round(gb * rahmen.b * massstab))
  leinwand.height = Math.max(1, Math.round(gh * rahmen.h * massstab))
  const g = leinwand.getContext('2d')!
  g.scale(massstab, massstab)
  g.translate(-gb * rahmen.x, -gh * rahmen.y)
  g.translate(gb / 2, gh / 2)
  g.rotate((drehung * Math.PI) / 180)
  g.drawImage(quelle, -breite / 2, -hoehe / 2)
  return leinwand
}

export function TresorBildeditor({ item, vorschauUrl, onFertig }: { item: VaultItem; vorschauUrl: string; onFertig: () => void }) {
  const { t } = useTranslation()
  const userKey = useVaultStore((s) => s.userKey)
  const dateiErsetzen = useVaultStore((s) => s.dateiErsetzen)
  const [drehung, setDrehung] = useState(0)
  const [rahmen, setRahmen] = useState<Rahmen>(GANZ)
  const [bild, setBild] = useState<HTMLImageElement | null>(null)
  const [speichert, setSpeichert] = useState(false)
  const flaeche = useRef<HTMLDivElement>(null)
  const zug = useRef<{ griff: Griff; x: number; y: number; start: Rahmen } | null>(null)

  useEffect(() => {
    const img = new Image()
    img.onload = () => setBild(img)
    img.src = vorschauUrl
  }, [vorschauUrl])

  const drehen = (um: number) => {
    setDrehung((d) => (d + um + 360) % 360)
    setRahmen(GANZ)
  }

  const quer = drehung % 180 !== 0
  const seitenverhaeltnis = bild ? (quer ? bild.naturalHeight / bild.naturalWidth : bild.naturalWidth / bild.naturalHeight) : 1

  const runter = (griff: Griff) => (e: React.PointerEvent) => {
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    zug.current = { griff, x: e.clientX, y: e.clientY, start: rahmen }
  }

  const bewegt = (e: React.PointerEvent) => {
    const z = zug.current
    const box = flaeche.current?.getBoundingClientRect()
    if (!z || !box || box.width === 0) return
    setRahmen(rahmenZiehen(z.start, z.griff, (e.clientX - z.x) / box.width, (e.clientY - z.y) / box.height))
  }

  const speichern = async () => {
    if (!userKey || !item.datei) return
    setSpeichert(true)
    try {
      const original = await blobLesen(item.datei.original, item.id, userKey, item.datei.typ)
      const bitmap = await createImageBitmap(original, { imageOrientation: 'from-image' })
      const pixel = bitmap.width * bitmap.height * rahmen.b * rahmen.h
      const massstab = Math.min(1, Math.sqrt(HOECHSTENS_PIXEL / pixel))
      const leinwand = zeichnen(bitmap, bitmap.width, bitmap.height, drehung, rahmen, massstab)
      bitmap.close()
      const typ = BEARBEITBAR.includes(item.datei.typ) ? item.datei.typ : 'image/jpeg'
      const ergebnis = await new Promise<Blob | null>((r) => leinwand.toBlob(r, typ, 0.92))
      leinwand.width = 0
      if (!ergebnis) throw new Error(t('mss.vault.bearbeiten.fehler'))
      await dateiErsetzen(item.id, ergebnis)
      toast.success(t('mss.vault.bearbeiten.gespeichert'))
      onFertig()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.bearbeiten.fehler'))
      setSpeichert(false)
    }
  }

  const griffe: Griff[] = ['nw', 'ne', 'sw', 'se']
  const unveraendert = drehung === 0 && rahmen === GANZ

  return (
    <Dialog open onOpenChange={(offen) => !offen && !speichert && onFertig()}>
      {/* Ab `md` füllt der Editor das Fenster; `--bildhoehe` ist der Platz zwischen Kopf und Fuß. */}
      <DialogContent
        className="max-w-3xl [--bildhoehe:60vh] md:h-[100dvh] md:max-w-none md:rounded-none md:border-0 md:[--bildhoehe:calc(100dvh-12rem)]"
        overlayClassName="md:p-0"
        data-testid="tresor-bildeditor"
      >
        <DialogHeader>
          <DialogTitle className="truncate">{t('mss.vault.bearbeiten.titel', { name: item.service })}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center justify-center bg-black/80 p-4 md:min-h-0 md:flex-1">
          {bild && (
            <div
              ref={flaeche}
              className="relative max-h-[var(--bildhoehe)] max-w-full touch-none select-none"
              style={{ aspectRatio: String(seitenverhaeltnis), width: `min(100%, calc(var(--bildhoehe) * ${seitenverhaeltnis}))` }}
              onPointerMove={bewegt}
              onPointerUp={() => (zug.current = null)}
              onPointerCancel={() => (zug.current = null)}
            >
              <img
                src={vorschauUrl}
                alt={item.service}
                draggable={false}
                className="absolute left-1/2 top-1/2 max-w-none"
                style={{
                  width: quer ? `${100 / seitenverhaeltnis}%` : '100%',
                  height: quer ? `${100 * seitenverhaeltnis}%` : '100%',
                  transform: `translate(-50%, -50%) rotate(${drehung}deg)`,
                }}
              />
              <div
                data-testid="zuschnitt"
                className="absolute cursor-move border-2 border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.55)]"
                style={{ left: `${rahmen.x * 100}%`, top: `${rahmen.y * 100}%`, width: `${rahmen.b * 100}%`, height: `${rahmen.h * 100}%` }}
                onPointerDown={runter('mitte')}
              >
                {/* Sichtbar 16 px, getroffen wird eine unsichtbare Fläche von 44 px drumherum. */}
                {griffe.map((g) => (
                  <span
                    key={g}
                    data-griff={g}
                    onPointerDown={runter(g)}
                    className={`absolute h-4 w-4 rounded-full border-2 border-white bg-primary before:absolute before:-inset-3.5 before:rounded-full ${g.includes('n') ? '-top-2' : '-bottom-2'} ${
                      g.includes('w') ? '-left-2' : '-right-2'
                    } ${g === 'nw' || g === 'se' ? 'cursor-nwse-resize' : 'cursor-nesw-resize'}`}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
        {/* Auf dem Telefon 44-px-Ziele (min-h/min-w, die Höhe aus `size` bleibt sonst stehen); ab sm wie gehabt. */}
        <DialogFooter className="flex-wrap justify-between gap-y-2 max-sm:[&_button]:min-h-11 max-sm:[&_button]:min-w-11">
          <div className="flex gap-1">
            <Button type="button" variant="ghost" size="icon" aria-label={t('mss.vault.bearbeiten.links')} onClick={() => drehen(-90)} disabled={speichert}>
              <RotateCcw className="h-4 w-4" />
            </Button>
            <Button type="button" variant="ghost" size="icon" aria-label={t('mss.vault.bearbeiten.rechts')} onClick={() => drehen(90)} disabled={speichert}>
              <RotateCw className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t('mss.vault.bearbeiten.zuruecksetzen')}
              onClick={() => {
                setDrehung(0)
                setRahmen(GANZ)
              }}
              disabled={speichert || unveraendert}
            >
              <Undo2 className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onFertig} disabled={speichert}>
              {t('common.cancel')}
            </Button>
            <Button type="button" onClick={() => void speichern()} disabled={speichert || unveraendert || !bild}>
              {speichert ? t('common.saving') : t('common.save')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
