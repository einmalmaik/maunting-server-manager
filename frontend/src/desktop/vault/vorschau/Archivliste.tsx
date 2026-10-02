/**
 * Was in einem Archiv steckt, ohne es zu entpacken: Pfade und Größen.
 * Auf dunklem Grund, für die Lichtbox.
 */
import { useTranslation } from 'react-i18next'
import { File as DateiIcon, Folder } from 'lucide-react'
import { formatBytes } from '@/lib/format'
import { ARCHIV_EINTRAEGE_HOECHSTENS, type ArchivEintrag } from '@/lib/zipLesen'

export function Archivliste({ eintraege, label }: { eintraege: ArchivEintrag[]; label: string }) {
  const { t } = useTranslation()
  const dateien = eintraege.filter((e) => !e.ordner)
  const summe = dateien.reduce((s, e) => s + e.groesse, 0)
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col px-2 sm:px-4">
      <p className="shrink-0 py-2 text-label-sm text-white/60">
        {t('mss.vault.dateien.archiv.zusammenfassung', { count: dateien.length, groesse: formatBytes(summe) })}
        {eintraege.length >= ARCHIV_EINTRAEGE_HOECHSTENS && ` · ${t('mss.vault.dateien.archiv.gekuerzt', { anzahl: ARCHIV_EINTRAEGE_HOECHSTENS.toLocaleString() })}`}
      </p>
      <ul aria-label={label} className="min-h-0 flex-1 overflow-auto rounded-lg border border-white/10 bg-white/[0.03]">
        {eintraege.map((e, i) => (
          <li key={i} className="flex items-center gap-2.5 border-b border-white/5 px-3 py-2 text-sm last:border-b-0">
            {e.ordner ? <Folder className="h-4 w-4 shrink-0 text-secondary" aria-hidden /> : <DateiIcon className="h-4 w-4 shrink-0 text-white/50" aria-hidden />}
            <span className="min-w-0 flex-1 break-all font-mono text-[0.8rem] text-white/90">{e.pfad}</span>
            {!e.ordner && <span className="shrink-0 tabular-nums text-label-sm text-white/55">{formatBytes(e.groesse)}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}
