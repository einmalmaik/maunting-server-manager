// @vitest-environment node
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

/**
 * Ein `title` an einem HTML-Element zeichnet das Betriebssystem: in seinem
 * eigenen Stil, erst nach einer Sekunde, am Finger nie (AGENTS.md Punkt 4).
 * Namen und Hinweise gehen über `aria-label` und `Kurzinfo`.
 *
 * Erlaubt ist `title` nur an Komponenten, die daraus eine sichtbare
 * Überschrift machen, und an `iframe`, wo es der zugängliche Name ist. Eine
 * Komponente, die `title` an ihr HTML weiterreicht (`Button`, `Badge`), steht
 * nicht in der Liste und fällt hier auf.
 */
const UEBERSCHRIFT = new Set([
  'AiKnowledgeShell',
  'Alert',
  'DocCard',
  'FormDialog',
  'ListBlock',
  'MapStatus',
  'PageHeader',
  'RegionalEmptyState',
  'Section',
  'SectionHeading',
  'SidebarDownloadBadge',
  'SocialPostList',
  'iframe',
])

const quellen = import.meta.glob(['/src/**/*.tsx', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function titelStellen(pfad: string, text: string): string[] {
  const datei = ts.createSourceFile(pfad, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const stellen: string[] = []
  const besuch = (knoten: ts.Node) => {
    if (ts.isJsxAttribute(knoten) && knoten.name.getText(datei) === 'title') {
      const tag = (knoten.parent.parent as ts.JsxOpeningLikeElement).tagName.getText(datei)
      if (!UEBERSCHRIFT.has(tag)) {
        const zeile = datei.getLineAndCharacterOfPosition(knoten.getStart(datei)).line + 1
        stellen.push(`${pfad}:${zeile} <${tag}>`)
      }
    }
    ts.forEachChild(knoten, besuch)
  }
  besuch(datei)
  return stellen
}

describe('kein natives title', () => {
  it('setzt an keinem Element ein title, das der Browser als Tooltip zeichnet', () => {
    // Bis 02.10.2026 standen 166 solche title im Frontend.
    const stellen = Object.entries(quellen).flatMap(([pfad, text]) => titelStellen(pfad, text))
    expect(stellen).toEqual([])
  })

  it('erkennt title an HTML und an weiterreichenden Komponenten', () => {
    const probe = `<>
      <button title="a" />
      <Button title={t('x')} />
      <Section title="Überschrift" />
      <iframe title="Vorschau" />
    </>`
    expect(titelStellen('probe.tsx', probe)).toEqual(['probe.tsx:2 <button>', 'probe.tsx:3 <Button>'])
  })
})
