/**
 * Wohin weitergeleitet werden soll.
 *
 * **Am Telefon eine eigene Ansicht, kein Dialog.** Eine Liste mit Suchfeld und
 * Mehrfachauswahl braucht die volle Höhe; in einem Kästchen in der Mitte wäre
 * sie drei Zeilen hoch und läge bei offener Tastatur darunter. Der Senden-Knopf
 * klebt am unteren Rand, innerhalb der sicheren Fläche.
 *
 * Zuletzt genutzte Chats stehen oben, weil man meistens an dieselben zwei Leute
 * weiterleitet.
 *
 * Während ein Anhang neu hochgeladen wird, läuft ein Fortschritt: über Mobilfunk
 * dauert das spürbar, und ein Knopf, der einfach nicht mehr reagiert, sieht aus
 * wie ein Fehler.
 *
 * **Per Portal an `document.body`**, aus demselben Grund wie bei der
 * `TrefferListe`: im Chat-Ast hängend gäbe es sie ohne offenen Chat nicht, und
 * `Shell.tsx` kappt jedes `z-50` im Inhaltsbereich auf 10.
 */

import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeft, Check, Forward, Search, Users } from 'lucide-react'

import { Avatar, Button, Input } from '@/Singra/UI'
import type { Weiterleitungsziel } from '@/services/nachrichtWeiterleiten'

export interface WeiterleitenAnsichtProps {
  offen: boolean
  onSchliessen: () => void
  /** Alle möglichen Ziele, schon in der Reihenfolge „zuletzt genutzt zuerst". */
  ziele: readonly (Weiterleitungsziel & { avatarUrl?: string | null; istGruppe?: boolean })[]
  /** Wie viele Nachrichten mitgehen — steht im Knopf. */
  anzahlNachrichten: number
  onSenden: (gewaehlt: Weiterleitungsziel[]) => Promise<void>
  /** `null`, solange nichts läuft. */
  fortschritt: { gesamt: number; fertig: number } | null
}

export function WeiterleitenAnsicht({
  offen,
  onSchliessen,
  ziele,
  anzahlNachrichten,
  onSenden,
  fortschritt,
}: WeiterleitenAnsichtProps) {
  const [suche, setSuche] = useState('')
  const [gewaehlt, setGewaehlt] = useState<string[]>([])
  const [laeuft, setLaeuft] = useState(false)

  const gefiltert = useMemo(() => {
    const s = suche.trim().toLowerCase()
    if (!s) return ziele
    return ziele.filter((z) => z.name.toLowerCase().includes(s))
  }, [ziele, suche])

  if (!offen || typeof document === 'undefined') return null

  const umschalten = (mid: string) =>
    setGewaehlt((v) => (v.includes(mid) ? v.filter((x) => x !== mid) : [...v, mid]))

  const senden = async () => {
    const auswahl = ziele.filter((z) => gewaehlt.includes(z.blindMailboxId))
    if (!auswahl.length || laeuft) return
    setLaeuft(true)
    try {
      await onSenden(auswahl)
      setGewaehlt([])
      setSuche('')
    } finally {
      setLaeuft(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col bg-surface">
      <div className="shrink-0 px-3 py-2.5 border-b border-outline-variant/20 flex items-center gap-2">
        <button
          type="button"
          onClick={onSchliessen}
          className="w-11 h-11 -ml-1 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors"
          aria-label="Weiterleiten abbrechen"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-on-surface truncate">Weiterleiten</div>
          <div className="text-[11px] text-on-surface-variant">
            {anzahlNachrichten === 1 ? 'Eine Nachricht' : `${anzahlNachrichten} Nachrichten`}
          </div>
        </div>
      </div>

      <div className="shrink-0 px-3 py-2">
        <Input
          value={suche}
          onChange={(e) => setSuche(e.target.value)}
          placeholder="Chat oder Gruppe suchen …"
          prefix={<Search className="w-4 h-4" />}
          aria-label="Ziel suchen"
        />
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-2">
        {gefiltert.length === 0 ? (
          <p className="py-10 text-center text-xs text-on-surface-variant/70">
            Kein Chat passt zu dieser Suche.
          </p>
        ) : (
          gefiltert.map((z) => {
            const an = gewaehlt.includes(z.blindMailboxId)
            return (
              <button
                key={z.blindMailboxId}
                type="button"
                onClick={() => umschalten(z.blindMailboxId)}
                className={`w-full min-h-14 px-2 py-2 rounded-xl flex items-center gap-3 text-left transition-colors ${
                  an ? 'bg-primary/15' : 'hover:bg-surface-container-high'
                }`}
                aria-pressed={an}
              >
                {z.istGruppe ? (
                  <span className="w-10 h-10 rounded-full bg-tertiary/20 flex items-center justify-center shrink-0">
                    <Users className="w-5 h-5 text-tertiary" />
                  </span>
                ) : (
                  <Avatar src={z.avatarUrl ?? null} name={z.name} size="md" className="w-10 h-10 shrink-0" />
                )}
                <span className="min-w-0 flex-1 truncate text-sm text-on-surface">{z.name}</span>
                <span
                  className={`w-6 h-6 rounded-full border flex items-center justify-center shrink-0 ${
                    an ? 'bg-primary border-primary text-on-primary' : 'border-outline-variant/60 text-transparent'
                  }`}
                  aria-hidden="true"
                >
                  <Check className="w-3.5 h-3.5" />
                </span>
              </button>
            )
          })
        )}
      </div>

      <div className="shrink-0 p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] border-t border-outline-variant/20 bg-surface-container-lowest space-y-2">
        {fortschritt && fortschritt.gesamt > 0 && (
          <div className="space-y-1">
            <div className="flex justify-between text-[11px] text-on-surface-variant">
              <span>Anhang wird neu verschlüsselt …</span>
              <span className="tabular-nums">
                {fortschritt.fertig} / {fortschritt.gesamt}
              </span>
            </div>
            <div className="h-1 rounded-full bg-surface-container-high overflow-hidden">
              <div
                className="h-full bg-primary transition-[width]"
                style={{ width: `${Math.round((fortschritt.fertig / fortschritt.gesamt) * 100)}%` }}
              />
            </div>
          </div>
        )}
        <Button
          type="button"
          variant="primary"
          disabled={gewaehlt.length === 0 || laeuft}
          onClick={() => void senden()}
          className="w-full min-h-12 gap-2"
        >
          <Forward className="w-4 h-4" />
          <span>
            {laeuft
              ? 'Wird gesendet …'
              : gewaehlt.length === 0
                ? 'Ziel wählen'
                : `An ${gewaehlt.length} ${gewaehlt.length === 1 ? 'Chat' : 'Chats'} senden`}
          </span>
        </Button>
      </div>
    </div>,
    document.body,
  )
}
