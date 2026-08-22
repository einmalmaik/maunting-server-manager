/**
 * Weiche zwischen den zwei Fenstern der App — ein Bundle, zwei Fenster.
 *
 * `?fenster=overlay` ist das frameless Always-on-Top-Fenster der Sprachblase
 * (tauri.conf.json), alles andere ist das Hauptfenster.
 */
import { useEffect } from 'react'

import {
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { DesktopApp } from './DesktopApp'
import { OverlayFenster } from './OverlayFenster'
import { konfigLaden } from './tauri'

export function DesktopRoot() {
  const overlay =
    new URLSearchParams(window.location.search).get('fenster') === 'overlay'

  // Gerätewahl und Mikrofon-Verarbeitung gelten in **beiden** Fenstern —
  // Sprachsitzungen laufen im Overlay wie im Hauptfenster. Die Einstellungen
  // registrieren Änderungen sofort nach; dieses eine Laden deckt den
  // Fensterstart ab.
  useEffect(() => {
    void konfigLaden()
      .then((konfig) => {
        registriereAudioGeraete(konfig.audio_eingabe, konfig.audio_ausgabe)
        registriereAudioVerarbeitung({
          echo: konfig.audio_echo,
          rauschen: konfig.audio_rauschen,
          autogain: konfig.audio_autogain,
          verstaerkung: konfig.audio_verstaerkung,
        })
      })
      .catch(() => undefined)
  }, [])

  return overlay ? <OverlayFenster /> : <DesktopApp />
}
