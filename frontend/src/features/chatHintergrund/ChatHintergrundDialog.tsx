import React, { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Image as ImageIcon, Layers, RotateCcw, Sliders, Upload } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'

import {
  ladeChatHintergrund,
  speichereChatHintergrund,
  STANDARD_HINTERGRUND,
  type ChatHintergrundBereich,
} from './speicher'
import { HintergrundFlaeche } from './ChatHintergrund'
import { HINTERGRUND_VORLAGEN, STANDARD_VORLAGE, type HintergrundId } from './vorlagen'

/** Was der Browser als eigenes Bild annimmt, und wie gross es werden darf. */
const ERLAUBTE_TYPEN = 'image/png,image/jpeg,image/webp'
const GROESSTES_BILD = 8 * 1024 * 1024

export interface ChatHintergrundDialogProps {
  /** Für welche Fläche gewählt wird — Messenger oder KI-Bereich. */
  bereich: ChatHintergrundBereich
  offen: boolean
  onOffenChange: (offen: boolean) => void
}

/**
 * Das Einstellungsfenster für den Chat-Hintergrund.
 *
 * Es steht genau einmal im Haus und wird von beiden Flächen benutzt. Was es
 * speichert, entscheidet allein `bereich` — die Oberfläche ist dieselbe, die
 * Antwort darf verschieden sein.
 *
 * Gelesen wird beim Öffnen, nicht beim Rendern des Elternteils: `Dialog`
 * zeichnet seine Kinder nur, solange es offen ist, also beginnt jeder Besuch
 * mit dem, was wirklich gespeichert ist. Ein danebenliegender Zustand im
 * Elternteil könnte veralten, sobald eine zweite Fläche denselben Bereich
 * ändert.
 */
export function ChatHintergrundDialog({ bereich, offen, onOffenChange }: ChatHintergrundDialogProps) {
  return (
    <Dialog open={offen} onOpenChange={onOffenChange}>
      <DialogInhalt bereich={bereich} onSchliessen={() => onOffenChange(false)} />
    </Dialog>
  )
}

function DialogInhalt({
  bereich,
  onSchliessen,
}: {
  bereich: ChatHintergrundBereich
  onSchliessen: () => void
}) {
  const { t } = useTranslation()
  const gespeichert = ladeChatHintergrund(bereich)

  const [gewaehlt, setzeGewaehlt] = useState<HintergrundId>(gespeichert.preset)
  const [eigenesBild, setzeEigenesBild] = useState<string | undefined>(gespeichert.customDataUrl)
  const [abdunkeln, setzeAbdunkeln] = useState<number>(gespeichert.dimLevel)
  // Schlüssel, kein fertiger Satz — sonst bliebe die Meldung nach einem
  // Sprachwechsel in der alten Sprache stehen.
  const [fehler, setzeFehler] = useState<string | null>(null)
  const dateiFeld = useRef<HTMLInputElement>(null)

  const nimmDatei = (ereignis: React.ChangeEvent<HTMLInputElement>) => {
    setzeFehler(null)
    const datei = ereignis.target.files?.[0]
    if (!datei) return

    if (!datei.type.startsWith('image/')) {
      setzeFehler('social.wallpaper.badType')
      return
    }

    if (datei.size > GROESSTES_BILD) {
      setzeFehler('social.wallpaper.tooLarge')
      return
    }

    const leser = new FileReader()
    leser.onload = () => {
      setzeEigenesBild(leser.result as string)
      setzeGewaehlt('custom')
    }
    leser.onerror = () => {
      setzeFehler('social.wallpaper.loadFailed')
    }
    leser.readAsDataURL(datei)
  }

  const uebernehmen = () => {
    const konnte = speichereChatHintergrund(bereich, {
      preset: gewaehlt,
      customDataUrl: gewaehlt === 'custom' ? eigenesBild : undefined,
      dimLevel: abdunkeln,
    })
    // Offen lassen und sagen, was ist: sonst sähe man den Hintergrund stehen
    // und fände ihn nach dem nächsten Neuladen nicht wieder.
    if (!konnte) {
      setzeFehler('social.wallpaper.saveFailed')
      return
    }
    onSchliessen()
  }

  const zuruecksetzen = () => {
    setzeGewaehlt(STANDARD_HINTERGRUND.preset)
    setzeEigenesBild(undefined)
    setzeAbdunkeln(STANDARD_HINTERGRUND.dimLevel)
    speichereChatHintergrund(bereich, STANDARD_HINTERGRUND)
    onSchliessen()
  }

  return (
    // Kein eigener Grund und kein eigener Innenabstand: `msm-card` bringt
    // Fläche, Rand und Radius mit, und `DialogHeader`/`DialogFooter` sind
    // Bänder über die volle Breite. Mit `p-5` am Fenster schwebte die Kopfzeile
    // als Kasten darin — ein Kasten im Kasten, den niemand gemeint hat.
    <DialogContent className="max-w-md w-full">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <ImageIcon className="w-5 h-5 shrink-0 text-primary" />
          <span>{t('social.wallpaper.title')}</span>
        </DialogTitle>
        <DialogDescription>{t('social.wallpaper.description')}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 px-5 py-4">
        {/* Die Vorlagen — eine Kachel je Eintrag des Katalogs */}
        <div className="space-y-3">
          <label className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-primary" />
            <span>{t('social.wallpaper.presets')}</span>
          </label>

          <div className="grid grid-cols-2 gap-2.5">
            {HINTERGRUND_VORLAGEN.map((vorlage) => {
              const aktiv = gewaehlt === vorlage.id
              const Zeichen = vorlage.icon
              return (
                <button
                  key={vorlage.id}
                  type="button"
                  onClick={() => setzeGewaehlt(vorlage.id)}
                  aria-pressed={aktiv}
                  // `min-h-24` statt `h-24`: in einem schmalen Fenster bricht
                  // „Farbverlauf von Maunting Studios" auf drei Zeilen um, und
                  // eine feste Höhe schnitt sie mitten im Wort ab.
                  className={`group relative p-3 rounded-xl border text-left flex flex-col justify-between gap-2 min-h-24 overflow-hidden transition-all ${
                    aktiv
                      ? 'border-primary ring-2 ring-primary/40 bg-primary/10'
                      : 'border-outline-variant/30 bg-surface-container-low hover:border-outline-variant/60'
                  }`}
                >
                  {/* Dieselbe Fläche, die nachher im Chat steht — keine
                      nachgebaute Vorschau, die auseinanderlaufen könnte. */}
                  <div className="absolute inset-0 pointer-events-none">
                    <HintergrundFlaeche vorlage={vorlage.id} />
                  </div>

                  <div className="relative z-10 flex items-center justify-between w-full">
                    <div className={`p-1 rounded-md ${vorlage.iconKlassen ?? 'bg-primary/15 text-primary'}`}>
                      <Zeichen className="w-3.5 h-3.5" />
                    </div>
                    {aktiv && (
                      <div className="w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center">
                        <Check className="w-2.5 h-2.5 stroke-[3]" />
                      </div>
                    )}
                  </div>
                  <div className="relative z-10">
                    <p className="text-xs font-bold text-on-surface flex flex-wrap items-center gap-1">
                      <span>{t(vorlage.nameKey)}</span>
                      {vorlage.id === STANDARD_VORLAGE && (
                        <span className="text-label-sm px-1 py-0.2 rounded bg-primary/20 text-primary font-mono">
                          {t('social.wallpaper.default')}
                        </span>
                      )}
                    </p>
                    <p className="text-label-sm text-on-surface-variant/80">{t(vorlage.hinweisKey)}</p>
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* Eigenes Bild */}
        <div className="space-y-2 pt-1 border-t border-outline-variant/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
              <Upload className="w-3.5 h-3.5 text-primary" />
              <span>{t('social.wallpaper.own')}</span>
            </span>
            {eigenesBild && (
              <span className="text-label-sm text-status-success font-medium flex items-center gap-1">
                <Check className="w-3 h-3" />
                <span>{t('social.wallpaper.stored')}</span>
              </span>
            )}
          </div>

          <input
            ref={dateiFeld}
            type="file"
            accept={ERLAUBTE_TYPEN}
            onChange={nimmDatei}
            className="hidden"
          />

          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant={gewaehlt === 'custom' ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => dateiFeld.current?.click()}
              className="text-xs gap-1.5 h-8 flex-1"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>{eigenesBild ? t('social.wallpaper.pickOther') : t('social.wallpaper.pickFirst')}</span>
            </Button>

            {eigenesBild && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setzeGewaehlt('custom')}
                className={`text-xs h-8 px-2.5 ${gewaehlt === 'custom' ? 'text-primary font-bold' : 'text-on-surface-variant'}`}
              >
                {t('social.wallpaper.activate')}
              </Button>
            )}
          </div>

          {eigenesBild && (
            <div className="relative h-16 w-full rounded-xl overflow-hidden border border-outline-variant/40 mt-1.5">
              <img src={eigenesBild} alt="" className="w-full h-full object-cover" />
              <div className="absolute inset-0 bg-black/25 flex items-center justify-center">
                <span className="text-label-sm font-medium text-white/90 bg-black/50 px-2 py-0.5 rounded-full">
                  {t('social.wallpaper.coverHint')}
                </span>
              </div>
            </div>
          )}

          {fehler && <p className="text-label-sm text-error">{t(fehler)}</p>}
        </div>

        {/* Abdunkeln */}
        <div className="space-y-1.5 pt-1 border-t border-outline-variant/20">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-on-surface flex items-center gap-1.5">
              <Sliders className="w-3.5 h-3.5 text-primary" />
              <span>{t('social.wallpaper.dim')}</span>
            </span>
            <span className="font-mono text-label-sm text-primary">{abdunkeln}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="75"
            step="5"
            value={abdunkeln}
            onChange={(ereignis) => setzeAbdunkeln(Number(ereignis.target.value))}
            aria-label={t('social.wallpaper.dim')}
            className="w-full accent-primary h-1.5 bg-surface-container-highest rounded-lg cursor-pointer"
          />
          <div className="flex justify-between text-label-sm text-on-surface-variant/60">
            <span>{t('social.wallpaper.dimLight')}</span>
            <span>{t('social.wallpaper.dimReadable')}</span>
            <span>{t('social.wallpaper.dimDark')}</span>
          </div>
        </div>
      </div>

      {/* `flex-wrap`, damit die Fusszeile auf einem 375-px-Telefon umbricht,
          statt „Übernehmen" über den Rand zu schieben. */}
      <DialogFooter className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={zuruecksetzen}
          className="text-xs h-8 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high gap-1 px-2.5"
          title={t('social.wallpaper.resetHint')}
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>{t('common.reset')}</span>
        </Button>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onSchliessen}
            className="inline-flex items-center justify-center h-8 px-3.5 text-xs font-medium rounded-lg border border-outline-variant/50 bg-surface-container-high hover:bg-surface-container-highest text-on-surface hover:border-outline transition-all active:scale-[0.98]"
          >
            {t('common.cancel')}
          </button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={uebernehmen}
            className="text-xs h-8 px-4 font-semibold"
          >
            {t('common.apply')}
          </Button>
        </div>
      </DialogFooter>
    </DialogContent>
  )
}
