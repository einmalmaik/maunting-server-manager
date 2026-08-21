/**
 * Weiche zwischen den zwei Fenstern der App — ein Bundle, zwei Fenster.
 *
 * `?fenster=overlay` ist das frameless Always-on-Top-Fenster der Sprachblase
 * (tauri.conf.json), alles andere ist das Hauptfenster.
 */
import { DesktopApp } from './DesktopApp'
import { OverlayFenster } from './OverlayFenster'

export function DesktopRoot() {
  const overlay =
    new URLSearchParams(window.location.search).get('fenster') === 'overlay'
  return overlay ? <OverlayFenster /> : <DesktopApp />
}
