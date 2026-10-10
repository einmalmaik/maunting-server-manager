/**
 * Der Rechtsklick in der App (Windows, macOS, Linux). Das Menü der WebView
 * („Aktualisieren“, „Speichern unter“, „Drucken“) fällt weg; an seine Stelle
 * tritt eines mit Kopieren und Einfügen, und nur mit dem, was gerade geht:
 * Kopieren, wenn Text markiert ist, Einfügen in einem Eingabefeld. Aus
 * Passwortfeldern wird nichts kopiert.
 *
 * Wer einen Rechtsklick selbst behandelt (`preventDefault`), etwa der
 * Messenger an einer Nachricht, behält ihn. Unter Android gibt es dieses
 * Menü nicht, dort bleibt die Textauswahl des Systems.
 */
import { useEffect, useState } from 'react'
import { ClipboardPaste, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kontextmenue } from '@/Singra/UI'
import type { ActionMenuItem } from '@/Singra/UI/ActionMenu'

const FELD = 'input, textarea, [contenteditable=""], [contenteditable="true"]'
const TEXTFELDER = new Set(['text', 'search', 'url', 'tel', 'email', 'password'])

type Feld = HTMLInputElement | HTMLTextAreaElement | HTMLElement

interface Offen {
  ort: { x: number; y: number }
  text: string
  feld: Feld | null
  bereich: Range | null
}

/** Das Eingabefeld unter dem Zeiger, wenn es Text annimmt. */
function feldVon(ziel: EventTarget | null): Feld | null {
  const feld = ziel instanceof Element ? ziel.closest(FELD) : null
  if (feld instanceof HTMLInputElement) return TEXTFELDER.has(feld.type) ? feld : null
  return feld instanceof HTMLElement ? feld : null
}

function markiert(feld: Feld | null): string {
  if (feld instanceof HTMLInputElement || feld instanceof HTMLTextAreaElement) {
    if (feld instanceof HTMLInputElement && feld.type === 'password') return ''
    return feld.value.slice(feld.selectionStart ?? 0, feld.selectionEnd ?? 0)
  }
  return window.getSelection()?.toString() ?? ''
}

function beschreibbar(feld: Feld | null): boolean {
  if (feld instanceof HTMLInputElement || feld instanceof HTMLTextAreaElement) return !feld.readOnly && !feld.disabled
  return feld !== null
}

async function einfuegen(feld: Feld, bereich: Range | null) {
  const text = await navigator.clipboard.readText()
  // Nach dem Warten kann das Feld weg sein (Dialog zu, Seite gewechselt).
  if (!text || !feld.isConnected) return
  feld.focus()
  if (bereich && !(feld instanceof HTMLInputElement || feld instanceof HTMLTextAreaElement)) {
    const auswahl = window.getSelection()
    auswahl?.removeAllRanges()
    auswahl?.addRange(bereich)
  }
  // `insertText` geht durch die Bearbeitung des Browsers: React sieht die
  // Eingabe, und Strg+Z nimmt sie zurück.
  document.execCommand('insertText', false, text)
}

export function TextMenue() {
  const { t } = useTranslation()
  const [offen, setOffen] = useState<Offen | null>(null)

  useEffect(() => {
    if (/android/i.test(navigator.userAgent)) return
    const rechtsklick = (e: MouseEvent) => {
      if (e.defaultPrevented) return
      e.preventDefault()
      const feld = feldVon(e.target)
      const auswahl = window.getSelection()
      setOffen({
        ort: { x: e.clientX, y: e.clientY },
        text: markiert(feld),
        feld: beschreibbar(feld) ? feld : null,
        bereich: auswahl && auswahl.rangeCount > 0 ? auswahl.getRangeAt(0).cloneRange() : null,
      })
    }
    document.addEventListener('contextmenu', rechtsklick)
    return () => document.removeEventListener('contextmenu', rechtsklick)
  }, [])

  const items: ActionMenuItem[] = []
  if (offen?.text) {
    const text = offen.text
    items.push({
      key: 'kopieren',
      label: t('mss.textmenue.kopieren'),
      icon: <Copy className="h-4 w-4" />,
      onSelect: () => void navigator.clipboard.writeText(text).catch(() => undefined),
    })
  }
  if (offen?.feld) {
    const { feld, bereich } = offen
    items.push({
      key: 'einfuegen',
      label: t('mss.textmenue.einfuegen'),
      icon: <ClipboardPaste className="h-4 w-4" />,
      onSelect: () => void einfuegen(feld, bereich).catch(() => undefined),
    })
  }

  if (!offen || items.length === 0) return null
  return <Kontextmenue ort={offen.ort} items={items} label={t('mss.textmenue.name')} onSchliessen={() => setOffen(null)} />
}
