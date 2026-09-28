// @vitest-environment node
/**
 * Wie eine Gruppe heisst — und wo dieser Name liegen darf.
 *
 * Seit Stufe 6 sind `chat_groups.name`, `description` und `avatar_url`
 * geräumt: der Server weiss nicht mehr, worum es in einer Gruppe geht. Dieser
 * Datei liegt die Gegenprobe dazu ob — dass der Name auf dem Weg zurück zur
 * Oberfläche nicht doch irgendwo im Klartext liegen bleibt.
 *
 * Drei Zusagen werden geprüft:
 *
 * 1. Was in `localStorage` steht, ist bei aktivem Siegel unlesbar.
 * 2. Ein verschlossener Messenger gibt keine Namen heraus — und lässt sie nach
 *    dem Entsperren wieder da sein, statt eine leere Karte zu zementieren.
 * 3. Eine Zeile gehört zu ihrer Gruppe. Umgehängt geht sie nicht mehr auf.
 *
 * Node-Umgebung: gebraucht wird WebCrypto und ein `localStorage`, kein DOM.
 */

import { generateAesGcmKey } from '@msdis/shield/aead'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** Ein `localStorage` im Arbeitsspeicher. Die Ablage dieser Datei liegt dort. */
const platte = new Map<string, string>()
;(globalThis as any).localStorage = {
  get length() {
    return platte.size
  },
  key: (i: number) => [...platte.keys()][i] ?? null,
  getItem: (k: string) => platte.get(k) ?? null,
  setItem: (k: string, v: string) => void platte.set(k, v),
  removeItem: (k: string) => void platte.delete(k),
  clear: () => platte.clear(),
}

/**
 * Der Gruppenblock als Attrappe.
 *
 * `gruppenName.ts` schreibt über `aendereGruppenzustand` — das ist der Punkt,
 * an dem die Rollen im selben Block nicht verloren gehen dürfen. Hier steht
 * deshalb ein Zustand, der sich merkt, was vorher drin war.
 */
const block = vi.hoisted(() => ({
  stand: null as Record<string, unknown> | null,
  /** `false` lässt jeden Schreibversuch scheitern — kein Schlüssel, kein Netz. */
  schreibbar: true,
  lesbar: true,
}))

vi.mock('./gruppenKonfig', () => ({
  leererGruppenzustand: () => ({ v: 1, name: null, beschreibung: null, logo: null, rollen: [], zuordnung: {} }),
  ladeGruppenzustand: async () =>
    block.lesbar && block.stand
      ? { art: 'zustand', zustand: block.stand, revision: 1, vonKonto: 1 }
      : { art: 'leer', revision: 0 },
  aendereGruppenzustand: async (
    _kontext: unknown,
    _darfSchreiben: unknown,
    aendere: (vorher: Record<string, unknown>) => Record<string, unknown>,
  ) => {
    if (!block.schreibbar) return { art: 'abgelehnt', grund: 'kein-schluessel' }
    const vorher = block.stand ?? {
      v: 1,
      name: null,
      beschreibung: null,
      logo: null,
      rollen: [],
      zuordnung: {},
    }
    block.stand = aendere(vorher)
    return { art: 'gespeichert', revision: 2 }
  },
}))

const kontext = {
  groupId: 7,
  blindMailboxId: 'a'.repeat(64),
  eigeneId: 1,
  mitglieder: [1, 2],
  istEigentuemer: true,
}

/** Ein Bild, das `alsBild` durchlässt. Der Inhalt ist bedeutungslos. */
const LOGO = 'data:image/png;base64,iVBORw0KGgo='

/**
 * Ein Neustart des Moduls — und damit auch der Sperre.
 *
 * `vi.resetModules()` gibt `gruppenName` einen **frischen** `lokaleVersiegelung`
 * mit leerem Inhaltsschlüssel. Genau das ist ein Neustart: der Schlüssel liegt
 * nur im Arbeitsspeicher und ist nach einem Neuladen weg, bis die PIN wieder
 * eingegeben wird. Deshalb kommt er hier als Argument zurück ins Modul — und
 * die Sperrschalter kommen aus derselben frischen Instanz, sonst zeigte der
 * Test auf eine, die das Modul gar nicht benutzt.
 */
async function frischImportieren(schluessel: CryptoKey | null = null) {
  vi.resetModules()
  // Auch das angemeldete Konto kommt frisch: ohne es kennt `siegelAktiv()`
  // keinen Schalter und gäbe immer `false` zurück — die halbe Datei prüfte
  // dann nichts.
  const konto = await import('@/lib/angemeldetesKonto')
  konto.setzeAngemeldetesKonto(10)
  const siegel = await import('./lokaleVersiegelung')
  siegel.setzeInhaltsSchluessel(schluessel)
  const modul = await import('./gruppenName')
  return { ...modul, ...siegel }
}

/** Siegel an: von hier an liegt nichts mehr im Klartext auf der Platte. */
async function mitSiegel() {
  const schluessel = await generateAesGcmKey()
  const modul = await frischImportieren(schluessel)
  modul.setzeSiegelAktiv(true)
  return { ...modul, schluessel }
}

describe('Der örtliche Namensspeicher', () => {
  beforeEach(() => {
    platte.clear()
    block.stand = null
    block.schreibbar = true
    block.lesbar = true
  })

  it('merkt sich einen Namen und gibt ihn der Gruppenliste zurück', async () => {
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()

    await merkeGruppenName(7, { name: 'Küchenplanung', beschreibung: 'Wer bringt was' })

    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('Küchenplanung')
    expect(gruppe.description).toBe('Wer bringt was')
  })

  it('lässt den Namen des Servers gewinnen, wenn es einen gibt', async () => {
    // Ein Panel, das noch nicht migriert ist, schickt weiter Klartext. Der ist
    // dann der frischere Stand — der örtliche Speicher füllt nur Lücken.
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(7, { name: 'alt' })

    const [gruppe] = await benenneGruppen([
      { id: 7, name: 'vom Server', description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('vom Server')
  })

  it('ordnet alphabetisch und schiebt Namenloses ans Ende', async () => {
    /*
     * Der Server gibt die Gruppen seit der Räumung nach Alter heraus — er
     * kennt keinen Namen mehr, nach dem er ordnen könnte. Alphabetisch geht
     * erst hier, hinter der Entschlüsselung.
     */
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(3, { name: 'Zeltlager' })
    await merkeGruppenName(1, { name: 'ärztliche Notfälle' })
    await merkeGruppenName(2, { name: 'Bauverein' })

    const benannt = await benenneGruppen([
      { id: 9, name: null, description: null, avatar_url: null },
      { id: 3, name: null, description: null, avatar_url: null },
      { id: 8, name: null, description: null, avatar_url: null },
      { id: 1, name: null, description: null, avatar_url: null },
      { id: 2, name: null, description: null, avatar_url: null },
    ] as never[])

    expect(benannt.map((g) => g.id)).toEqual([1, 2, 3, 8, 9])
  })

  it('lässt eine unbekannte Gruppe unangetastet', async () => {
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(7, { name: 'Küchenplanung' })

    const liste = [{ id: 8, name: null, description: null, avatar_url: null }] as never[]
    const [gruppe] = await benenneGruppen(liste)
    expect(gruppe.name).toBeNull()
  })

  it('schreibt den Namen nicht im Klartext auf die Platte', async () => {
    // Die eigentliche Zusage. Ein Gruppenname in einem offenen
    // localStorage-Eintrag wäre genau die Metadatenzeile, die Stufe 6 aus der
    // Datenbank entfernt hat — nur auf einer anderen Platte.
    const { merkeGruppenName } = await mitSiegel()

    await merkeGruppenName(7, { name: 'Anwalt Sorgerecht', beschreibung: 'Termine' })

    const roh = platte.get('msm:gruppennamen')
    expect(roh).toBeTruthy()
    expect(roh).not.toContain('Anwalt')
    expect(roh).not.toContain('Sorgerecht')
    expect(roh).not.toContain('Termine')
  })

  it('überlebt einen Neustart', async () => {
    const erst = await mitSiegel()
    await erst.merkeGruppenName(7, { name: 'Küchenplanung', logo: LOGO })

    // Neustart: das Modul kommt frisch, die Platte bleibt — und die PIN wird
    // wieder eingegeben, also kommt derselbe Schlüssel zurück.
    const zweit = await frischImportieren(erst.schluessel)
    const [gruppe] = await zweit.benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('Küchenplanung')
    expect(gruppe.avatar_url).toBe(LOGO)
  })

  it('gibt bei verschlossenem Messenger nichts heraus — und danach wieder alles', async () => {
    const erst = await mitSiegel()
    await erst.merkeGruppenName(7, { name: 'Küchenplanung' })

    // Neustart mit Siegel, ohne Schlüssel: so sieht es vor der PIN aus.
    const zweit = await frischImportieren(null)
    const liste = [{ id: 7, name: null, description: null, avatar_url: null }] as never[]
    expect((await zweit.benenneGruppen(liste))[0].name).toBeNull()

    /*
     * Und jetzt der Fall, der ohne die Marke `standWarZu` schiefginge: der
     * erste Aufruf fiel vor der PIN und legte eine leere Karte an. Käme die
     * aus dem Cache, bliebe die Liste bis zum Neuladen der Seite namenlos,
     * obwohl der Schlüssel längst da ist.
     */
    zweit.setzeInhaltsSchluessel(erst.schluessel)
    expect((await zweit.benenneGruppen(liste))[0].name).toBe('Küchenplanung')
  })

  it('schreibt keinen Klartext, wenn das Siegel aktiv, aber zu ist', async () => {
    const modul = await mitSiegel()
    await modul.merkeGruppenName(7, { name: 'Küchenplanung' })
    const vorher = platte.get('msm:gruppennamen')

    modul.setzeInhaltsSchluessel(null)
    await modul.merkeGruppenName(8, { name: 'Steuerberater' })

    // Nichts geschrieben — kein Klartext, und auch die bestehende Zeile bleibt.
    expect(platte.get('msm:gruppennamen')).toBe(vorher)
    expect(vorher).not.toContain('Steuerberater')
  })

  it('öffnet eine Zeile nicht unter einer fremden Gruppenkennung', async () => {
    // Die Bindung ist die AAD `msm-gruppenname:<id>`. Ohne sie liesse sich der
    // Name einer Gruppe unter der Kennung einer anderen unterschieben — und
    // ein Mensch schriebe in die falsche.
    const erst = await mitSiegel()
    await erst.merkeGruppenName(7, { name: 'Küchenplanung' })

    const vertauscht = JSON.parse(platte.get('msm:gruppennamen')!)
    platte.set('msm:gruppennamen', JSON.stringify({ '9': vertauscht['7'] }))

    const zweit = await frischImportieren(erst.schluessel)
    const [gruppe] = await zweit.benenneGruppen([
      { id: 9, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBeNull()
  })

  it('nimmt eine kaputte Zeile nicht die anderen mit', async () => {
    const erst = await mitSiegel()
    await erst.merkeGruppenName(7, { name: 'Küchenplanung' })
    await erst.merkeGruppenName(8, { name: 'Sportverein' })

    const roh = JSON.parse(platte.get('msm:gruppennamen')!)
    roh['7'] = { v: 1, blob: 'nicht-entschluesselbar' }
    platte.set('msm:gruppennamen', JSON.stringify(roh))

    const zweit = await frischImportieren(erst.schluessel)
    const liste = [
      { id: 7, name: null, description: null, avatar_url: null },
      { id: 8, name: null, description: null, avatar_url: null },
    ] as never[]
    // Nach Kennung suchen, nicht nach Platz: `benenneGruppen` sortiert.
    const benannt = await zweit.benenneGruppen(liste)
    expect(benannt.find((g) => g.id === 7)!.name).toBeNull()
    expect(benannt.find((g) => g.id === 8)!.name).toBe('Sportverein')
  })

  it('vergisst eine Gruppe beim Austritt und alle beim Abmelden', async () => {
    const modul = await mitSiegel()
    await modul.merkeGruppenName(7, { name: 'Küchenplanung' })
    await modul.merkeGruppenName(8, { name: 'Sportverein' })

    await modul.vergissGruppenName(7)
    const liste = [
      { id: 7, name: null, description: null, avatar_url: null },
      { id: 8, name: null, description: null, avatar_url: null },
    ] as never[]
    let benannt = await modul.benenneGruppen(liste)
    expect(benannt.find((g) => g.id === 7)!.name).toBeNull()
    expect(benannt.find((g) => g.id === 8)!.name).toBe('Sportverein')
    // Und die Platte, nicht nur der Arbeitsspeicher.
    expect(Object.keys(JSON.parse(platte.get('msm:gruppennamen')!))).toEqual(['8'])

    modul.leereGruppenNamen()
    expect(platte.get('msm:gruppennamen')).toBeUndefined()
    benannt = await modul.benenneGruppen(liste)
    expect(benannt.find((g) => g.id === 8)!.name).toBeNull()
  })

  it('weist eine Zeile ohne jeden Inhalt ab', async () => {
    // Sonst läge für jede je geöffnete Gruppe eine leere Zeile da — eine
    // Liste der Gruppen dieses Menschen, ohne einen einzigen Namen darin.
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(7, { name: '   ', beschreibung: null, logo: null })
    expect(platte.get('msm:gruppennamen')).toBeUndefined()

    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBeNull()
  })

  it('nimmt nur Bild-Data-URLs als Logo', async () => {
    // Das Logo kommt aus einer Einladungskarte, also von einem anderen
    // Menschen. `data:text/html,…` wäre in einem `<img src>` harmlos, in einem
    // späteren `window.open` nicht.
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(7, { name: 'Küchenplanung', logo: 'data:text/html,<script>' })

    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('Küchenplanung')
    expect(gruppe.avatar_url).toBeNull()
  })

  it('kürzt einen überlangen Namen, statt ihn zu übernehmen', async () => {
    const { benenneGruppen, merkeGruppenName } = await frischImportieren()
    await merkeGruppenName(7, { name: 'x'.repeat(500) })

    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toHaveLength(64)
  })
})

describe('Der verschlüsselte Gruppenblock', () => {
  beforeEach(() => {
    platte.clear()
    block.stand = null
    block.schreibbar = true
    block.lesbar = true
  })

  it('schreibt Name, Beschreibung und Logo in den Block', async () => {
    const { sichereGruppenAnsicht } = await frischImportieren()

    const ok = await sichereGruppenAnsicht(kontext, {
      name: 'Küchenplanung',
      beschreibung: 'Wer bringt was',
      logo: LOGO,
    })

    expect(ok).toBe(true)
    expect(block.stand).toMatchObject({
      name: 'Küchenplanung',
      beschreibung: 'Wer bringt was',
      logo: LOGO,
    })
  })

  it('lässt die Rollen im selben Block stehen', async () => {
    /*
     * Der Block ist **einer**. Wer ihn mit einem frisch gebauten Stand
     * überschriebe, löschte die Rechtetabelle mit — und zwar für alle
     * Mitglieder, nicht nur für sich. Deshalb läuft der Schreibweg über
     * `aendereGruppenzustand` und nicht über `schreibeGruppenzustand`.
     */
    block.stand = {
      v: 1,
      name: null,
      beschreibung: null,
      logo: null,
      rollen: [{ id: 'r1', name: 'Küchenchef', rechte: ['send_messages'] }],
      zuordnung: { '2': ['r1'] },
    }
    const { sichereGruppenAnsicht } = await frischImportieren()

    await sichereGruppenAnsicht(kontext, { name: 'Küchenplanung' })

    expect(block.stand).toMatchObject({
      name: 'Küchenplanung',
      rollen: [{ id: 'r1', name: 'Küchenchef', rechte: ['send_messages'] }],
      zuordnung: { '2': ['r1'] },
    })
  })

  it('merkt den Namen auch dann örtlich, wenn der Block nicht zu schreiben ist', async () => {
    // Eine frisch angelegte Gruppe hat oft noch kein Geheimnis — das entsteht
    // beim ersten Senden. Bis dahin ist der örtliche Speicher der einzige Ort,
    // an dem der Name steht, und ein `false` darf das Anlegen nicht abbrechen.
    block.schreibbar = false
    const { benenneGruppen, sichereGruppenAnsicht } = await frischImportieren()

    const ok = await sichereGruppenAnsicht(kontext, { name: 'Küchenplanung' })

    expect(ok).toBe(false)
    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('Küchenplanung')
  })

  it('holt den Namen aus dem Block und merkt ihn örtlich', async () => {
    // Der Weg für ein frisch eingerichtetes Gerät: der örtliche Speicher ist
    // leer, und der Block ist die einzige Quelle.
    block.stand = { v: 1, name: 'Küchenplanung', beschreibung: null, logo: LOGO, rollen: [], zuordnung: {} }
    const { benenneGruppen, holeGruppenAnsicht } = await frischImportieren()

    const ansicht = await holeGruppenAnsicht(kontext)

    expect(ansicht?.name).toBe('Küchenplanung')
    const [gruppe] = await benenneGruppen([
      { id: 7, name: null, description: null, avatar_url: null } as never,
    ])
    expect(gruppe.name).toBe('Küchenplanung')
    expect(gruppe.avatar_url).toBe(LOGO)
  })

  it('gibt null zurück, wenn der Block fehlt oder nicht zu lesen ist', async () => {
    block.lesbar = false
    const { holeGruppenAnsicht } = await frischImportieren()
    expect(await holeGruppenAnsicht(kontext)).toBeNull()
  })
})
