/**
 * Bietet eine Adresse oder einen Blob im Browser als Download an. Bis
 * 02.10.2026 stand dieser Anker achtmal im Code, mal ohne `appendChild`
 * (Firefox klickt einen losen Anker nicht), mal mit sofortigem Widerruf der
 * Blob-Adresse, der den Download in manchen Browsern abbrach.
 */
export function adresseHerunterladen(url: string, name: string): void {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

/** Die Blob-Adresse lebt eine Minute, dann ist der Download längst gestartet. */
export function blobHerunterladen(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  adresseHerunterladen(url, name)
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
