import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { useDownloadsStore } from './downloadsStore'

/** Sofortiges Feedback, auch für Downloads eines schon geschlossenen Popups.
 * Nur ein neuer Eintrag öffnet die Liste, nie Fortschritt oder Fertigmeldung:
 * wer sie schließt, wird während des Ladens nicht wieder hineingezogen.
 */
export function useDownloadHinweis() {
  const navigate = useNavigate()
  useEffect(
    () =>
      useDownloadsStore.subscribe((jetzt, vorher) => {
        if (jetzt.downloads.some((d) => !vorher.downloads.some((alt) => alt.nr === d.nr))) {
          navigate('/downloads')
        }
      }),
    [navigate],
  )
}
