/**
 * Was die App mit einer entschlüsselten Datei anfangen kann: selbst zeigen
 * oder auf dem Gerät speichern. Gemeinsam für Dateien und Fotos.
 */

/** Was der Tresor selbst zeigen kann. Alles andere wird gespeichert. */
export function anzeigeArt(typ: string): 'bild' | 'video' | 'audio' | 'text' | null {
  if (typ.startsWith('image/') && typ !== 'image/svg+xml') return 'bild'
  if (typ.startsWith('video/')) return 'video'
  if (typ.startsWith('audio/')) return 'audio'
  if (typ.startsWith('text/') || typ === 'application/json') return 'text'
  return null
}

/** Bietet eine Objekt-URL zum Speichern an (Download-Dialog der WebView). */
export function speichernUnter(url: string, name: string): void {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}
