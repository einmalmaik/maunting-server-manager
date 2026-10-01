/**
 * Zeigt eine Schriftdatei mit Probetext. Die Schrift wird aus den Bytes
 * geladen (`FontFace` mit Puffer), nicht über eine Adresse, und beim Schließen
 * wieder aus dem Dokument genommen.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

let zaehler = 0

export function Schriftprobe({ daten }: { daten: Uint8Array }) {
  const { t } = useTranslation()
  const [familie, setFamilie] = useState<string | null>(null)
  const [fehler, setFehler] = useState(false)

  useEffect(() => {
    const name = `msm-schriftprobe-${++zaehler}`
    // Eine Kopie: FontFace darf den Puffer übernehmen, der Aufrufer behält seinen.
    const schrift = new FontFace(name, daten.slice().buffer as ArrayBuffer)
    let vorbei = false
    setFamilie(null)
    setFehler(false)
    schrift
      .load()
      .then(() => {
        if (vorbei) return
        document.fonts.add(schrift)
        setFamilie(name)
      })
      .catch(() => !vorbei && setFehler(true))
    return () => {
      vorbei = true
      document.fonts.delete(schrift)
    }
  }, [daten])

  if (fehler) return <p className="p-6 text-center text-sm text-white/70">{t('mss.vault.dateien.schrift.fehler')}</p>
  if (!familie) return null
  const probe = t('mss.vault.dateien.schrift.probe')
  return (
    <div className="h-full w-full overflow-auto">
      <div className="mx-auto max-w-4xl space-y-6 px-4 py-6 text-white sm:px-8" style={{ fontFamily: `"${familie}", system-ui` }}>
        <p className="break-words text-5xl leading-tight sm:text-6xl">Aa Bb Cc Ää Öö Üü ß</p>
        <p className="break-words text-2xl">ABCDEFGHIJKLMNOPQRSTUVWXYZ</p>
        <p className="break-words text-2xl">abcdefghijklmnopqrstuvwxyz</p>
        <p className="break-words text-2xl">0123456789 &amp; ! ? @ € % ( ) „ “ – …</p>
        {[36, 24, 18, 14, 12].map((px) => (
          <p key={px} className="break-words" style={{ fontSize: px }}>
            <span className="mr-3 align-middle font-sans text-label-sm text-white/45">{px} px</span>
            {probe}
          </p>
        ))}
      </div>
    </div>
  )
}
