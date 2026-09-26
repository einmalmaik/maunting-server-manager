import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Link2, Plus, Trash2 } from 'lucide-react'
import { ActionMenu, Badge, Button, Switch } from '@/Singra/UI'
import { prompt } from '@/stores/promptStore'
import { useStudio } from './StudioContext'
import { AddColumnDialog, ColumnTypeDialog, ConstraintDialog, IndexDialog, PartitionDialog, type ConstraintKind } from './StructureDialogs'
import { Empty, Section, formatNumber } from './shared'
import { formatBytes, type StudioRelation, type StudioTableDetails } from './studioApi'

export function TableStructure({ table, relations, enums, onOpenTable }: {
  table: StudioTableDetails
  relations: StudioRelation[]
  enums: string[]
  onOpenTable: (schema: string, name: string) => void
}) {
  const { t, i18n } = useTranslation()
  const { runOperation, canAdmin } = useStudio()
  const [dialog, setDialog] = useState<
    | { kind: 'column' }
    | { kind: 'type'; column: string }
    | { kind: 'constraint'; constraint: ConstraintKind }
    | { kind: 'index' }
    | { kind: 'partition' }
    | null
  >(null)
  const close = () => setDialog(null)
  const isTable = table.kind === 'r' || table.kind === 'p'
  const alter = (actions: unknown[], title: string) =>
    runOperation({ op: 'alter_table', schema_name: table.schema, name: table.table, actions }, { title })

  const renameColumn = async (column: string) => {
    const next = await prompt({ title: t('postgresStudio.structure.renameColumn'), message: t('postgresStudio.structure.renameColumnMessage', { column }), defaultValue: column })
    if (next && next !== column) await alter([{ action: 'rename_column', column, new_name: next }], t('postgresStudio.structure.renameColumn'))
  }
  const setDefault = async (column: string, current: string | null) => {
    const next = await prompt({ title: t('postgresStudio.structure.setDefault'), message: t('postgresStudio.structure.setDefaultMessage', { column }), defaultValue: current || '', placeholder: 'now()' })
    if (next !== null) await alter([{ action: 'set_default', column, default: next.trim() || null }], t('postgresStudio.structure.setDefault'))
  }
  const comment = async (column: string | null, current: string | null) => {
    const next = await prompt({ title: t('postgresStudio.structure.comment'), message: column ? t('postgresStudio.structure.commentColumn', { column }) : t('postgresStudio.structure.commentTable'), defaultValue: current || '' })
    if (next !== null) await alter([{ action: 'set_comment', column, comment: next.trim() || null }], t('postgresStudio.structure.comment'))
  }
  const renameTable = async () => {
    const next = await prompt({ title: t('postgresStudio.structure.renameTable'), message: t('postgresStudio.structure.renameTableMessage', { table: table.table }), defaultValue: table.table })
    if (next && next !== table.table && (await alter([{ action: 'rename_table', new_name: next }], t('postgresStudio.structure.renameTable')))) {
      onOpenTable(table.schema, next)
    }
  }

  const fk = table.constraints.filter((c) => c.type === 'foreign_key')
  return (
    <div className="space-y-4">
      <Section
        title={t('postgresStudio.structure.columns')}
        actions={
          canAdmin && isTable ? (
            <>
              <Button size="sm" onClick={() => setDialog({ kind: 'column' })} data-testid="add-column-button">
                <Plus className="h-3.5 w-3.5" />
                {t('postgresStudio.structure.addColumn')}
              </Button>
              <ActionMenu
                label={t('postgresStudio.structure.tableActions')}
                items={[
                  { key: 'rename', label: t('postgresStudio.structure.renameTable'), onSelect: () => void renameTable() },
                  { key: 'comment', label: t('postgresStudio.structure.commentTable'), onSelect: () => void comment(null, table.comment) },
                ]}
              />
            </>
          ) : undefined
        }
      >
        {table.comment && <p className="mb-3 text-sm text-on-surface-variant">{table.comment}</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-on-surface-variant">
              <tr className="border-b border-outline-variant">
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.columns.name')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.columns.type')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.columns.notNull')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.columns.default')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.designer.comment')}</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {table.columns.map((column) => {
                const pk = table.primary_key.includes(column.name)
                const ref = fk.find((c) => c.columns.includes(column.name))
                return (
                  <tr key={column.name} className="border-b border-outline-variant/50 align-top">
                    <td className="py-2 pr-3 font-mono text-on-surface">
                      <span className="inline-flex items-center gap-1.5">
                        {pk && <KeyRound className="h-3.5 w-3.5 text-status-warning" aria-label={t('postgresStudio.columns.primary')} />}
                        {ref && <Link2 className="h-3.5 w-3.5 text-secondary" aria-label={t('postgresStudio.fk.title')} />}
                        {column.name}
                      </span>
                    </td>
                    <td className="py-2 pr-3 font-mono text-secondary">
                      {column.type}
                      {column.identity && <Badge className="ml-2" variant="info">identity</Badge>}
                      {column.generated && <Badge className="ml-2">generated</Badge>}
                    </td>
                    <td className="py-2 pr-3">
                      <Switch
                        checked={!column.nullable}
                        disabled={!canAdmin || !isTable || pk}
                        onCheckedChange={(checked) => void alter([{ action: 'set_nullable', column: column.name, nullable: !checked }], t('postgresStudio.columns.notNull'))}
                        aria-label={t('postgresStudio.columns.notNull')}
                      />
                    </td>
                    <td className="max-w-48 truncate py-2 pr-3 font-mono text-on-surface-variant">{column.default || '–'}</td>
                    <td className="max-w-48 truncate py-2 pr-3 text-on-surface-variant">{column.comment || ''}</td>
                    <td className="py-2 text-right">
                      {canAdmin && isTable && (
                        <ActionMenu
                          compact
                          label={t('postgresStudio.structure.columnActions', { column: column.name })}
                          items={[
                            { key: 'rename', label: t('postgresStudio.structure.renameColumn'), onSelect: () => void renameColumn(column.name) },
                            { key: 'type', label: t('postgresStudio.structure.changeTypeShort'), onSelect: () => setDialog({ kind: 'type', column: column.name }) },
                            { key: 'default', label: t('postgresStudio.structure.setDefault'), onSelect: () => void setDefault(column.name, column.default) },
                            { key: 'comment', label: t('postgresStudio.structure.comment'), onSelect: () => void comment(column.name, column.comment) },
                            {
                              key: 'drop',
                              label: t('postgresStudio.structure.dropColumn'),
                              destructive: true,
                              separatorBefore: true,
                              onSelect: () => void alter([{ action: 'drop_column', column: column.name }], t('postgresStudio.structure.dropColumn')),
                            },
                          ]}
                        />
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Section>

      {isTable && (
        <Section
          title={t('postgresStudio.structure.constraints')}
          actions={
            canAdmin ? (
              <ActionMenu
                label={t('postgresStudio.structure.addConstraint')}
                items={(['primary_key', 'unique', 'check', 'foreign_key'] as ConstraintKind[])
                  .filter((kind) => kind !== 'primary_key' || !table.primary_key.length)
                  .map((kind) => ({ key: kind, label: t(`postgresStudio.structure.add.${kind}`), onSelect: () => setDialog({ kind: 'constraint', constraint: kind }) }))}
              />
            ) : undefined
          }
        >
          {table.constraints.filter((c) => c.type !== 'not_null').length === 0 ? (
            <Empty>{t('postgresStudio.structure.noConstraints')}</Empty>
          ) : (
            <ul className="space-y-2">
              {table.constraints
                .filter((c) => c.type !== 'not_null')
                .map((constraint) => (
                  <li key={constraint.name} className="flex items-start justify-between gap-3 rounded-lg border border-outline-variant p-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-on-surface">{constraint.name}</span>
                        <Badge variant={constraint.type === 'foreign_key' ? 'info' : 'default'}>{t(`postgresStudio.constraintTypes.${constraint.type}`)}</Badge>
                        {constraint.type === 'foreign_key' && (
                          <span className="text-xs text-on-surface-variant">
                            {t('postgresStudio.fk.onDelete')}: {t(`postgresStudio.fk.actions.${constraint.on_delete}`)} · {t('postgresStudio.fk.onUpdate')}: {t(`postgresStudio.fk.actions.${constraint.on_update}`)}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 break-words font-mono text-xs text-on-surface-variant">{constraint.definition}</p>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {constraint.type === 'foreign_key' && constraint.ref_table && (
                        <Button size="sm" variant="ghost" onClick={() => onOpenTable(constraint.ref_schema || table.schema, constraint.ref_table!)}>
                          {constraint.ref_table}
                        </Button>
                      )}
                      {canAdmin && (
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={t('postgresStudio.structure.dropConstraint')}
                          onClick={() => void alter([{ action: 'drop_constraint', name: constraint.name }], t('postgresStudio.structure.dropConstraint'))}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
            </ul>
          )}
          {table.referenced_by.length > 0 && (
            <div className="mt-4">
              <h4 className="mb-2 text-xs font-semibold text-on-surface-variant">{t('postgresStudio.structure.referencedBy')}</h4>
              <div className="flex flex-wrap gap-2">
                {table.referenced_by.map((ref) => (
                  <Button key={`${ref.schema}.${ref.table}.${ref.name}`} size="sm" variant="secondary" onClick={() => onOpenTable(ref.schema, ref.table)}>
                    <Link2 className="h-3.5 w-3.5" />
                    {ref.schema}.{ref.table} ({ref.columns.join(', ')})
                  </Button>
                ))}
              </div>
            </div>
          )}
        </Section>
      )}

      {(isTable || table.kind === 'm') && (
        <Section
          title={t('postgresStudio.indexes.title')}
          actions={
            canAdmin ? (
              <Button size="sm" variant="secondary" onClick={() => setDialog({ kind: 'index' })} data-testid="create-index-button">
                <Plus className="h-3.5 w-3.5" />
                {t('postgresStudio.indexes.create')}
              </Button>
            ) : undefined
          }
        >
          {table.indexes.length === 0 ? (
            <Empty>{t('postgresStudio.indexes.none')}</Empty>
          ) : (
            <ul className="space-y-2">
              {table.indexes.map((index) => (
                <li key={index.name} className="flex items-start justify-between gap-3 rounded-lg border border-outline-variant p-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-on-surface">{index.name}</span>
                      <Badge>{index.method}</Badge>
                      {index.primary && <Badge variant="warning">PK</Badge>}
                      {index.unique && !index.primary && <Badge variant="info">UNIQUE</Badge>}
                      {!index.valid && <Badge variant="destructive">{t('postgresStudio.indexes.invalid')}</Badge>}
                      <span className="text-xs text-on-surface-variant">
                        {formatBytes(index.size_bytes, i18n.language)} · {t('postgresStudio.indexes.scans', { count: index.scans ?? 0 })}
                      </span>
                    </div>
                    <p className="mt-1 break-words font-mono text-xs text-on-surface-variant">{index.definition}</p>
                  </div>
                  {canAdmin && !index.primary && (
                    <ActionMenu
                      compact
                      label={t('postgresStudio.indexes.actions', { name: index.name })}
                      items={[
                        { key: 'reindex', label: t('postgresStudio.indexes.reindex'), onSelect: () => void runOperation({ op: 'reindex', target: 'index', schema_name: table.schema, name: index.name, concurrently: true }, { title: t('postgresStudio.indexes.reindex') }) },
                        { key: 'drop', label: t('postgresStudio.indexes.drop'), destructive: true, separatorBefore: true, onSelect: () => void runOperation({ op: 'drop_index', schema_name: table.schema, name: index.name, concurrently: true }, { title: t('postgresStudio.indexes.drop') }) },
                      ]}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {table.kind === 'p' && (
        <Section
          title={t('postgresStudio.partitions.title')}
          actions={
            canAdmin ? (
              <Button size="sm" variant="secondary" onClick={() => setDialog({ kind: 'partition' })}>
                <Plus className="h-3.5 w-3.5" />
                {t('postgresStudio.partitions.create')}
              </Button>
            ) : undefined
          }
        >
          <p className="mb-2 font-mono text-xs text-on-surface-variant">{table.partition_key}</p>
          {table.partitions.length === 0 ? (
            <Empty>{t('postgresStudio.partitions.none')}</Empty>
          ) : (
            <ul className="space-y-2">
              {table.partitions.map((partition) => (
                <li key={partition.name} className="flex items-center justify-between gap-3 rounded-lg border border-outline-variant p-3">
                  <button type="button" className="min-w-0 text-left" onClick={() => onOpenTable(table.schema, partition.name)}>
                    <span className="font-mono text-xs text-secondary">{partition.name}</span>
                    <span className="ml-2 font-mono text-xs text-on-surface-variant">{partition.bound}</span>
                    <span className="ml-2 text-xs text-on-surface-variant">~{formatNumber(partition.estimated_rows, i18n.language)}</span>
                  </button>
                  {canAdmin && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void runOperation({ op: 'detach_partition', schema_name: table.schema, parent: table.table, name: partition.name }, { title: t('postgresStudio.partitions.detach') })}
                    >
                      {t('postgresStudio.partitions.detach')}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {table.view_definition && (
        <Section title={t('postgresStudio.views.definition')}>
          <pre className="whitespace-pre-wrap break-words rounded-lg bg-surface-container-lowest p-3 font-mono text-xs text-on-surface">{table.view_definition}</pre>
        </Section>
      )}

      <AddColumnDialog open={dialog?.kind === 'column'} onClose={close} table={table} enums={enums} />
      {dialog?.kind === 'type' && <ColumnTypeDialog open onClose={close} table={table} column={dialog.column} enums={enums} />}
      {dialog?.kind === 'constraint' && <ConstraintDialog open onClose={close} table={table} kind={dialog.constraint} relations={relations} />}
      <IndexDialog open={dialog?.kind === 'index'} onClose={close} table={table} />
      {table.kind === 'p' && <PartitionDialog open={dialog?.kind === 'partition'} onClose={close} table={table} />}
    </div>
  )
}
