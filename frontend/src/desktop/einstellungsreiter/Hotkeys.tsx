import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { hotkeysSetzen, konfigLaden } from '../tauri'

/**
 * Baut aus einem Tastendruck die Kombination im Format der Registrierung
 * („Ctrl+Shift+K"). `null` heißt: nur Modifier gedrückt — weiter warten.
 * Der eigentliche Prüfer sitzt in Rust (`hotkey_pruefen`); was er ablehnt,
 * kommt als Fehlermeldung zurück, und der alte Hotkey bleibt.
 */
function komboAusEreignis(ereignis: KeyboardEvent): string | null {
  const code = ereignis.code
  if (!code || /^(Control|Alt|Shift|Meta)/.test(code)) return null
  const teile: string[] = []
  if (ereignis.ctrlKey) teile.push('Ctrl')
  if (ereignis.altKey) teile.push('Alt')
  if (ereignis.shiftKey) teile.push('Shift')
  if (ereignis.metaKey) teile.push('Super')
  const taste = code.startsWith('Key')
    ? code.slice(3)
    : code.startsWith('Digit')
      ? code.slice(5)
      : code
  return [...teile, taste].join('+')
}

/** Die Vorgaben — dieselben Werte wie `konfig::Default` in Rust. */
const HOTKEY_VORGABEN = { fenster: 'Alt+Space', sprache: 'Alt+Shift+Space' } as const

type HotkeyArt = 'fenster' | 'sprache'

/**
 * Zwei Hotkeys, je einzeln abschaltbar: einer fürs Hauptfenster, einer für
 * die Sprachsitzung im Overlay. Geändert wird per Aufnahme (nächster
 * Tastendruck), registriert und gespeichert in Rust (`hotkeys_setzen`) —
 * eine belegte Kombination kommt als Fehler zurück, nichts stellt sich um.
 */
export function Hotkeys() {
  const { t } = useTranslation()
  const [werte, setWerte] = useState<Record<HotkeyArt, string | null> | null>(null)
  const [aufnahme, setAufnahme] = useState<HotkeyArt | null>(null)
  // Die letzte Kombination je Seite: der Aktiv-Schalter stellt sie wieder
  // her — nicht die Werksvorgabe, die der Benutzer längst ersetzt hat.
  const zuletzt = useRef({ ...HOTKEY_VORGABEN } as Record<HotkeyArt, string>)

  useEffect(() => {
    void konfigLaden()
      .then((konfig) => {
        const geladen = { fenster: konfig.hotkey_fenster, sprache: konfig.hotkey_sprache }
        if (geladen.fenster) zuletzt.current.fenster = geladen.fenster
        if (geladen.sprache) zuletzt.current.sprache = geladen.sprache
        setWerte(geladen)
      })
      .catch(() => setWerte(null))
  }, [])

  async function anwenden(neu: Record<HotkeyArt, string | null>) {
    try {
      await hotkeysSetzen(neu.fenster, neu.sprache)
      if (neu.fenster) zuletzt.current.fenster = neu.fenster
      if (neu.sprache) zuletzt.current.sprache = neu.sprache
      setWerte(neu)
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  useEffect(() => {
    if (!aufnahme || !werte) return
    const taste = (ereignis: KeyboardEvent) => {
      ereignis.preventDefault()
      ereignis.stopPropagation()
      if (ereignis.key === 'Escape') {
        setAufnahme(null)
        return
      }
      const kombi = komboAusEreignis(ereignis)
      if (!kombi) return
      setAufnahme(null)
      void anwenden({ ...werte, [aufnahme]: kombi })
    }
    window.addEventListener('keydown', taste, true)
    return () => window.removeEventListener('keydown', taste, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aufnahme, werte])

  const zeile = (art: HotkeyArt) => {
    const wert = werte?.[art] ?? null
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.einstellungen.hotkey.${art}`)}</p>
          <p className="text-xs text-on-surface-variant">
            {wert ? <kbd className="font-mono">{wert}</kbd> : t('mss.einstellungen.hotkey.aus')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!werte || wert === null}
            onClick={() => setAufnahme(aufnahme === art ? null : art)}
          >
            {aufnahme === art
              ? t('mss.einstellungen.hotkey.druecken')
              : t('mss.einstellungen.hotkey.aendern')}
          </Button>
          <Switch
            checked={wert !== null}
            disabled={!werte}
            onCheckedChange={(an) => {
              if (!werte) return
              setAufnahme(null)
              void anwenden({ ...werte, [art]: an ? zuletzt.current[art] : null })
            }}
            aria-label={t(`mss.einstellungen.hotkey.${art}`)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.einstellungen.hotkey.titel')}</p>
      </div>
      {zeile('fenster')}
      {zeile('sprache')}
    </div>
  )
}
