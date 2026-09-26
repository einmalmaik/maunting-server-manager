import { useTranslation } from 'react-i18next'
import type { Json } from './studioApi'

type PlanNode = Record<string, Json> & { Plans?: PlanNode[] }

/**
 * EXPLAIN (FORMAT JSON) als Baum. Knoten mit hohem Anteil an der Gesamtzeit
 * (ANALYZE) oder an den Gesamtkosten werden hervorgehoben.
 */
export function PlanTree({ plan }: { plan: Json }) {
  const { t } = useTranslation()
  const root = Array.isArray(plan) ? (plan[0] as Record<string, Json>) : null
  const node = root?.Plan as PlanNode | undefined
  if (!node) return <p className="text-sm text-on-surface-variant">{t('postgresStudio.explain.noPlan')}</p>
  const totalTime = Number(root?.['Execution Time'] ?? node['Actual Total Time'] ?? 0)
  const totalCost = Number(node['Total Cost'] ?? 0)
  return (
    <div className="space-y-2" data-testid="plan-tree">
      <div className="flex flex-wrap gap-4 text-xs text-on-surface-variant">
        {root?.['Planning Time'] != null && <span>{t('postgresStudio.explain.planning')}: {String(root['Planning Time'])} ms</span>}
        {root?.['Execution Time'] != null && <span>{t('postgresStudio.explain.execution')}: {String(root['Execution Time'])} ms</span>}
        <span>{t('postgresStudio.explain.cost')}: {totalCost}</span>
      </div>
      <ul className="space-y-1">
        <PlanItem node={node} totalTime={totalTime} totalCost={totalCost} depth={0} />
      </ul>
    </div>
  )
}

function PlanItem({ node, totalTime, totalCost, depth }: { node: PlanNode; totalTime: number; totalCost: number; depth: number }) {
  const { t } = useTranslation()
  const actual = node['Actual Total Time'] != null ? Number(node['Actual Total Time']) * Number(node['Actual Loops'] ?? 1) : null
  const share = actual != null && totalTime > 0 ? actual / totalTime : totalCost > 0 ? Number(node['Total Cost'] ?? 0) / totalCost : 0
  const hot = share >= 0.5 ? 'border-status-destructive/60 bg-status-destructive/10' : share >= 0.2 ? 'border-status-warning/60 bg-status-warning/10' : 'border-outline-variant'
  const relation = node['Relation Name'] ? ` ${t('postgresStudio.explain.on')} ${String(node['Relation Name'])}` : ''
  const index = node['Index Name'] ? ` (${String(node['Index Name'])})` : ''
  const rowsPlanned = node['Plan Rows']
  const rowsActual = node['Actual Rows']
  const details = ['Filter', 'Index Cond', 'Hash Cond', 'Join Filter', 'Recheck Cond', 'Sort Key', 'Group Key']
    .filter((key) => node[key] != null)
    .map((key) => `${key}: ${Array.isArray(node[key]) ? (node[key] as Json[]).join(', ') : String(node[key])}`)
  const buffers = node['Shared Hit Blocks'] != null ? `${t('postgresStudio.explain.buffers')}: ${String(node['Shared Hit Blocks'])} hit / ${String(node['Shared Read Blocks'] ?? 0)} read` : null
  return (
    <li>
      <div className={`rounded-md border p-2 ${hot}`} style={{ marginLeft: depth * 16 }}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="font-mono text-xs font-semibold text-on-surface">
            {String(node['Node Type'])}
            {relation}
            {index}
          </span>
          <span className="font-mono text-label-sm text-on-surface-variant">
            {actual != null ? `${actual.toFixed(2)} ms · ` : ''}
            {t('postgresStudio.explain.rows')}: {rowsActual != null ? `${String(rowsActual)} / ` : ''}
            {String(rowsPlanned ?? '–')} · {t('postgresStudio.explain.cost')}: {String(node['Total Cost'] ?? '–')}
          </span>
        </div>
        {details.map((line) => (
          <p key={line} className="mt-0.5 break-words font-mono text-label-sm text-on-surface-variant">{line}</p>
        ))}
        {buffers && <p className="mt-0.5 font-mono text-label-sm text-on-surface-variant">{buffers}</p>}
      </div>
      {node.Plans && (
        <ul className="mt-1 space-y-1">
          {node.Plans.map((child, i) => (
            <PlanItem key={i} node={child} totalTime={totalTime} totalCost={totalCost} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  )
}
