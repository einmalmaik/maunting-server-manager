// @vitest-environment node
/**
 * Die verschlüsselte Einladungskarte.
 *
 * Node-Umgebung, nicht jsdom: DIS prüft Eingaben mit `instanceof Uint8Array`.
 * Die Krypto ist hier **echt** — AES-256-GCM aus DIS —, weil genau sie der
 * Gegenstand ist. Gestellt wird nichts.
 */

import { describe, expect, it } from 'vitest'

import {
  EINLADUNG_PREFIX,
  baueEinladungsKarte,
  einladungsschluesselAus,
  istEinladungsschluessel,
  lieseEinladungsKarte,
  mitSchluessel,
  schluesselAusLink,
} from './einladungsKarte'

const GEHEIMNIS = 'A'.repeat(43) + '='
const ANDERES = 'B'.repeat(43) + '='
const CODE = 'AbCd1234efGH'

const INHALT = { name: 'Serverteam', beschreibung: 'Wir bauen Dinge', logo: null }

describe('Der Einladungsschlüssel', () => {
  it('fällt aus dem Gruppengeheimnis und ist 64 Hexzeichen', async () => {
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    expect(schluessel).toMatch(/^[0-9a-f]{64}$/)
    expect(istEinladungsschluessel(schluessel)).toBe(true)
  })

  it('ist ein anderer Wert als Mailbox-Kennung und Besitznachweis', async () => {
    /*
     * Der Grund für den dritten Vorsatz. Wäre der Einladungsschlüssel
     * derselbe Wert wie die Mailbox-Kennung oder der Nachweis, gäbe man mit
     * jeder Einladung den Zugang zur Gruppenmailbox mit heraus — an jeden,
     * der den Link weiterleitet.
     */
    const { mailboxAusGeheimnis, nachweisAusGeheimnis } = await import('./gruppenSchluessel')
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)

    expect(schluessel).not.toBe(await mailboxAusGeheimnis(GEHEIMNIS))
    expect(schluessel).not.toBe(await nachweisAusGeheimnis(GEHEIMNIS))
  })

  it('ist für jedes Geheimnis ein anderer', async () => {
    expect(await einladungsschluesselAus(GEHEIMNIS)).not.toBe(
      await einladungsschluesselAus(ANDERES),
    )
  })
})

describe('Karte bauen und lesen', () => {
  it('geht auf und zurück', async () => {
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, INHALT)
    expect(karte.startsWith(EINLADUNG_PREFIX)).toBe(true)

    const gelesen = await lieseEinladungsKarte(
      karte,
      await einladungsschluesselAus(GEHEIMNIS),
      CODE,
    )
    expect(gelesen).toEqual({ name: 'Serverteam', beschreibung: 'Wir bauen Dinge', logo: null })
  })

  it('trägt den Klartext nicht im Umschlag', async () => {
    // Die eine Zusage, die eigentlich zählt: der Server legt diese Zeile ab
    // und liefert sie ohne Anmeldung an jeden mit dem Code aus.
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, INHALT)
    expect(karte).not.toContain('Serverteam')
    expect(karte).not.toContain('Wir bauen Dinge')
  })

  it('bleibt mit dem falschen Schlüssel zu', async () => {
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, INHALT)
    const fremd = await einladungsschluesselAus(ANDERES)

    expect(await lieseEinladungsKarte(karte, fremd, CODE)).toBeNull()
  })

  it('bleibt unter einem fremden Einladungscode zu', async () => {
    /*
     * Der Code steht im AAD. Ohne diese Bindung liesse sich die Karte einer
     * Gruppe unter dem Code einer anderen ablegen — die Vorschau zeigte dann
     * eine Gruppe, in die der Link gar nicht führt.
     */
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, INHALT)
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)

    expect(await lieseEinladungsKarte(karte, schluessel, 'ZzZz9999yYyY')).toBeNull()
  })

  it('wird nach einem Codewechsel unlesbar', async () => {
    // Das ist, was „Einladung zurückziehen" heisst: ein weitergereichter
    // Altlink zeigt danach nichts mehr.
    const alt = await baueEinladungsKarte(GEHEIMNIS, CODE, INHALT)
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    const neuerCode = 'NeuerCode123'

    expect(await lieseEinladungsKarte(alt, schluessel, neuerCode)).toBeNull()
    // Und die frische Karte unter dem neuen Code geht auf.
    const neu = await baueEinladungsKarte(GEHEIMNIS, neuerCode, INHALT)
    expect((await lieseEinladungsKarte(neu, schluessel, neuerCode))?.name).toBe('Serverteam')
  })

  it('antwortet auf jeden Fehlgrund gleich', async () => {
    /*
     * Kein Unterschied zwischen „falscher Schlüssel", „kein Umschlag" und
     * „Altbestand". Eine Unterscheidung wäre ein Auskunftsdienst über fremde
     * Einladungscodes — und der Aufrufer zeigt ohnehin in jedem Fall dasselbe.
     */
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    expect(await lieseEinladungsKarte(null, schluessel, CODE)).toBeNull()
    expect(await lieseEinladungsKarte('irgendwas', schluessel, CODE)).toBeNull()
    expect(await lieseEinladungsKarte(`${EINLADUNG_PREFIX}kaputt`, schluessel, CODE)).toBeNull()
    expect(await lieseEinladungsKarte(`${EINLADUNG_PREFIX}abc`, 'kein-schluessel', CODE)).toBeNull()
  })

  it('verwirft ein Logo, das kein Bild ist', async () => {
    /*
     * Die Karte kommt von einem anderen Menschen. `data:text/html,…` wäre in
     * einem `<img src>` harmlos, in einem späteren `<a href>` oder
     * `window.open` nicht. Die Schranke steht am Lesen, damit sie nicht an
     * jeder Anzeige einzeln stehen muss.
     */
    const boese = await baueEinladungsKarte(GEHEIMNIS, CODE, {
      name: 'Gruppe',
      logo: 'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
    })
    const gelesen = await lieseEinladungsKarte(
      boese,
      await einladungsschluesselAus(GEHEIMNIS),
      CODE,
    )

    // Die Karte selbst bleibt gültig — nur das Logo fällt weg. Eine Einladung
    // wegen eines krummen Bildes ganz zu verwerfen, wäre unverhältnismässig.
    expect(gelesen?.name).toBe('Gruppe')
    expect(gelesen?.logo).toBeNull()
  })

  it('nimmt ein echtes Bild als Logo an', async () => {
    // Die Gegenprobe. Ohne sie wäre die Schranke oben beliebig streng und
    // niemandem fiele auf, dass gar kein Logo mehr durchkommt.
    const echt = 'data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=='
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, { name: 'Gruppe', logo: echt })
    const gelesen = await lieseEinladungsKarte(
      karte,
      await einladungsschluesselAus(GEHEIMNIS),
      CODE,
    )

    expect(gelesen?.logo).toBe(echt)
  })

  it('verträgt eine Gruppe ohne Namen und ohne Logo', async () => {
    const karte = await baueEinladungsKarte(GEHEIMNIS, CODE, {})
    const gelesen = await lieseEinladungsKarte(
      karte,
      await einladungsschluesselAus(GEHEIMNIS),
      CODE,
    )

    expect(gelesen).toEqual({ name: null, beschreibung: null, logo: null })
  })
})

describe('Der Schlüssel im Link', () => {
  it('hängt hinter der Raute', async () => {
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    const link = mitSchluessel(`https://panel.test/chat/join/${CODE}`, schluessel)

    expect(link).toBe(`https://panel.test/chat/join/${CODE}#k=${schluessel}`)
    // Der Pfad, den der Server zu sehen bekommt, endet vor der Raute.
    expect(new URL(link).pathname).toBe(`/chat/join/${CODE}`)
    expect(new URL(link).search).toBe('')
  })

  it('wird aus einem Link wieder herausgelesen', async () => {
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    const link = mitSchluessel(`https://panel.test/chat/join/${CODE}`, schluessel)

    expect(schluesselAusLink(link)).toBe(schluessel)
    expect(schluesselAusLink(`https://panel.test/chat/join/${CODE}`)).toBeNull()
    expect(schluesselAusLink('#k=zukurz')).toBeNull()
  })
})
