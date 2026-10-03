// @vitest-environment node
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

/**
 * `Switch` ist ein `<button role="switch">` ohne eigenen Text. Steht sein Name
 * nur daneben in einem `<span>`, liest ein Screenreader „Schalter, aus“ und
 * sonst nichts. Jeder Schalter nennt seinen Namen per `aria-labelledby` auf
 * den sichtbaren Text oder per `aria-label`, oder er steckt in einem `<label>`
 * bzw. trägt eine `id`, auf die ein `<label htmlFor>` zeigt.
 */
const quellen = import.meta.glob(['/src/**/*.tsx', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const NAMEN = new Set(['aria-label', 'aria-labelledby'])

function schalterOhneNamen(pfad: string, text: string): string[] {
  const datei = ts.createSourceFile(pfad, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const stellen: string[] = []
  const inLabel = (knoten: ts.Node): boolean => {
    for (let k = knoten.parent; k; k = k.parent) {
      if (ts.isJsxElement(k) && k.openingElement.tagName.getText(datei) === 'label') return true
    }
    return false
  }
  // Eine `id` benennt nur, wenn ein `<label htmlFor>` derselben Datei auf sie zeigt.
  const ziele = new Set<string>()
  const sammle = (knoten: ts.Node) => {
    if (ts.isJsxAttribute(knoten) && knoten.name.getText(datei) === 'htmlFor' && knoten.initializer) {
      ziele.add(knoten.initializer.getText(datei))
    }
    ts.forEachChild(knoten, sammle)
  }
  sammle(datei)
  const benennt = (a: ts.JsxAttributeLike) => {
    if (ts.isJsxSpreadAttribute(a)) return true
    const name = a.name.getText(datei)
    if (name === 'id') return !!a.initializer && ziele.has(a.initializer.getText(datei))
    return NAMEN.has(name)
  }
  const besuch = (knoten: ts.Node) => {
    if ((ts.isJsxSelfClosingElement(knoten) || ts.isJsxOpeningElement(knoten)) && knoten.tagName.getText(datei) === 'Switch') {
      const benannt = knoten.attributes.properties.some(benennt)
      if (!benannt && !inLabel(knoten)) {
        const zeile = datei.getLineAndCharacterOfPosition(knoten.getStart(datei)).line + 1
        stellen.push(`${pfad}:${zeile}`)
      }
    }
    ts.forEachChild(knoten, besuch)
  }
  besuch(datei)
  return stellen
}

describe('Schalter mit Namen', () => {
  it('jeder Switch nennt seinen Namen', () => {
    // Bis 03.10.2026 lasen Screenreader an vielen Schaltern nur „Schalter, aus“.
    const stellen = Object.entries(quellen).flatMap(([pfad, text]) => schalterOhneNamen(pfad, text))
    expect(stellen).toEqual([])
  })

  it('erkennt Schalter ohne Namen und lässt benannte stehen', () => {
    const probe = `<>
      <span>Fensterwechsel</span><Switch checked={a} />
      <Switch checked={a} aria-labelledby="x" />
      <Switch checked={a} aria-label={t('y')} />
      <label><Switch checked={a} /> Text</label>
      <Switch checked={a} {...rest} />
    </>`
    expect(schalterOhneNamen('probe.tsx', probe)).toEqual(['probe.tsx:2'])
  })
})
