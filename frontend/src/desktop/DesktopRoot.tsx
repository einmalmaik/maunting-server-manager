/**
 * Weiche zwischen den zwei Fenstern der App — ein Bundle, zwei Fenster.
 *
 * `?fenster=overlay` ist das frameless Always-on-Top-Fenster der Sprachblase
 * (tauri.conf.json), alles andere ist das Hauptfenster.
 */
import { useEffect } from 'react'

import { registriereAudioGeraete } from '@/components/ai/voice/audioGeraete'
import { DesktopApp } from './DesktopApp'
import { OverlayFenster } from './OverlayFenster'
import { konfigLaden } from './tauri'

export function DesktopRoot() {
  const overlay =
    new URLSearchParams(window.location.search).get('fenster') === 'overlay'

  // Die Gerätewahl gilt in **beiden** Fenstern — Sprachsitzungen laufen im
  // Overlay wie im Hauptfenster. Die Einstellungen registrieren Änderungen
  // sofort nach; dieses eine Laden deckt den Fensterstart ab.
  useEffect(() => {
    void konfigLaden()
      .then((konfig) => registriereAudioGeraete(konfig.audio_eingabe, konfig.audio_ausgabe))
      .catch(() => undefined)
  }, [])

  return overlay ? <OverlayFenster /> : <DesktopApp />
}
