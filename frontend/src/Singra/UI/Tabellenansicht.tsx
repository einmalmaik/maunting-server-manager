/**
 * Zeigt Zeilen (etwa aus einer CSV) als Tabelle. Die erste Zeile ist der Kopf
 * und bleibt beim Scrollen stehen. Mehr als `hoechstens` Zeilen werden nicht
 * gezeichnet; darunter steht, wie viele es insgesamt sind.
 */
import { useTranslation } from 'react-i18next'

export interface TabellenansichtProps {
  zeilen: string[][]
  hoechstens?: number
  label: string
}

export function Tabellenansicht({ zeilen, hoechstens = 1000, label }: TabellenansichtProps) {
  const { t } = useTranslation()
  if (zeilen.length === 0) return <p className="p-6 text-sm text-on-surface-variant">{t('common.tabelle.leer')}</p>
  const [kopf, ...rest] = zeilen
  const spalten = Math.max(...zeilen.slice(0, hoechstens + 1).map((z) => z.length))
  const gezeigt = rest.slice(0, hoechstens)
  const zelle = 'max-w-[24rem] whitespace-pre-wrap break-words border-b border-r border-outline-variant/40 px-2.5 py-1.5 align-top'
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table aria-label={label} className="min-w-full border-separate border-spacing-0 text-sm">
          <thead className="sticky top-0 z-10 bg-surface-container-high">
            <tr>
              <th scope="col" className="sticky left-0 z-20 w-12 border-b border-r border-outline-variant/40 bg-surface-container-high px-2 py-1.5 text-right text-label-sm font-normal text-on-surface-variant">
                #
              </th>
              {Array.from({ length: spalten }, (_, i) => (
                <th key={i} scope="col" className={`${zelle} text-left font-semibold text-on-surface`}>
                  {kopf[i] ?? ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {gezeigt.map((zeile, nr) => (
              <tr key={nr} className="odd:bg-surface-container-low/40 hover:bg-surface-container-high/60">
                <td className="sticky left-0 border-b border-r border-outline-variant/40 bg-surface px-2 py-1.5 text-right font-mono text-label-sm text-on-surface-variant">
                  {nr + 1}
                </td>
                {Array.from({ length: spalten }, (_, i) => (
                  <td key={i} className={`${zelle} text-on-surface`}>
                    {zeile[i] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rest.length > hoechstens && (
        <p className="shrink-0 border-t border-outline-variant/40 px-3 py-2 text-label-sm text-on-surface-variant">
          {t('common.tabelle.gekuerzt', { gezeigt: hoechstens.toLocaleString(), gesamt: rest.length.toLocaleString() })}
        </p>
      )}
    </div>
  )
}
