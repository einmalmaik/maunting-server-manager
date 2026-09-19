/**
 * @vitest-environment node
 *
 * Der Messenger-PIN.
 *
 * Die vier Ablagen sind hier gefälscht: was sie tun, steht in ihren eigenen
 * Tests. Geprüft wird das, was nur hier passieren kann — dass aus einem PIN ein
 * Schlüssel wird, dass ein falscher PIN keinen ergibt, dass ein Wechsel den
 * Bestand nicht anfasst, und dass ein verlorener Schlüsselspeicher etwas
 * anderes meldet als ein Tippfehler.
 *
 * Argon2id rechnet mit 128 MiB. Jede PIN-Eingabe in dieser Datei kostet
 * deshalb spürbar Zeit — das ist der Sinn der Sache und der Grund für die
 * großzügigen Fristen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'

const fach = {
  schluesselfachVerfuegbar: vi.fn(async () => false),
  biometrieMoeglich: vi.fn(async () => false),
  geraeteGeheimnis: vi.fn(async (_erzeugen?: boolean) => null as Uint8Array | null),
  vergissGeraeteGeheimnis: vi.fn(async () => {}),
  verwahrePin: vi.fn(async (_pin: string) => {}),
  rufePinAb: vi.fn(async () => null as string | null),
  vergissPin: vi.fn(async () => {}),
}
vi.mock('./geraeteSchluesselfach', () => fach)

const umstellung = {
  nachrichten: vi.fn(async () => 0),
  geraet: vi.fn(async () => true),
  ratchet: vi.fn(async () => 0),
  gruppen: vi.fn(async () => 0),
}
vi.mock('./messengerLocalStore', () => ({
  schreibeNachrichtenBestandNeu: umstellung.nachrichten,
}))
const geraeteSpeicherGeleert = vi.fn()
vi.mock('./e2eeGeraet', () => ({
  schreibeGeraetBestandNeu: umstellung.geraet,
  clearGeraeteMemory: geraeteSpeicherGeleert,
}))
vi.mock('./ratchetSpeicher', () => ({ schreibeRatchetBestandNeu: umstellung.ratchet }))
vi.mock('./gruppenSchluessel', () => ({ schreibeGruppenBestandNeu: umstellung.gruppen }))

const { istOffen, siegelAktiv } = await import('./lokaleVersiegelung')
const { PIN_MINDESTLAENGE, useMessengerSperre } = await import('./messengerSperre')

const PIN = 'geheim-123'
const FRIST = 120_000

function installiereAblage() {
  const daten = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => daten.set(k, v),
    removeItem: (k: string) => daten.delete(k),
    clear: () => daten.clear(),
  }
}

describe('messengerSperre', () => {
  beforeEach(() => {
    installiereAblage()
    setzeAngemeldetesKonto(1)
    vi.clearAllMocks()
    fach.schluesselfachVerfuegbar.mockResolvedValue(false)
    fach.geraeteGeheimnis.mockResolvedValue(null)
    useMessengerSperre.setState({
      eingerichtet: false,
      entsperrt: true,
      laeuft: false,
      fehler: null,
      fehlversuche: 0,
      gesperrtBis: 0,
      biometrieAktiv: false,
      geraetebindung: false,
    })
  })

  it('weist einen zu kurzen PIN ab, bevor irgendetwas passiert', async () => {
    const kurz = 'a'.repeat(PIN_MINDESTLAENGE - 1)
    await expect(useMessengerSperre.getState().einrichten(kurz)).rejects.toThrow()
    expect(siegelAktiv()).toBe(false)
    expect(umstellung.nachrichten).not.toHaveBeenCalled()
  })

  it(
    'richtet ein, stellt den Bestand um und ist danach offen',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)

      const stand = useMessengerSperre.getState()
      expect(stand.eingerichtet).toBe(true)
      expect(stand.entsperrt).toBe(true)
      expect(siegelAktiv()).toBe(true)

      // Alle vier Ablagen müssen durchlaufen worden sein. Eine vergessene
      // bliebe im Klartext liegen, ohne dass es jemandem auffiele.
      expect(umstellung.nachrichten).toHaveBeenCalledOnce()
      expect(umstellung.geraet).toHaveBeenCalledOnce()
      expect(umstellung.ratchet).toHaveBeenCalledOnce()
      expect(umstellung.gruppen).toHaveBeenCalledOnce()
    },
    FRIST,
  )

  it(
    'sperrt, öffnet mit dem richtigen PIN und bleibt beim falschen zu',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)

      useMessengerSperre.getState().sperren()
      expect(useMessengerSperre.getState().entsperrt).toBe(false)
      expect(istOffen()).toBe(false)

      expect(await useMessengerSperre.getState().entsperren('falsch-falsch')).toBe(false)
      expect(useMessengerSperre.getState().entsperrt).toBe(false)
      expect(useMessengerSperre.getState().fehler).toBe('Falscher PIN.')
      expect(istOffen()).toBe(false)

      expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(true)
      expect(useMessengerSperre.getState().entsperrt).toBe(true)
      expect(useMessengerSperre.getState().fehlversuche).toBe(0)
      expect(istOffen()).toBe(true)
    },
    FRIST,
  )

  it(
    'nimmt beim Sperren auch den Geräteausweis aus dem Arbeitsspeicher',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      geraeteSpeicherGeleert.mockClear()

      useMessengerSperre.getState().sperren()

      // Bliebe der Ausweis im Speicher, hätte der Messenger nach der
      // automatischen Sperre weiter alles, was er zum Entschlüsseln braucht.
      expect(geraeteSpeicherGeleert).toHaveBeenCalledOnce()
    },
    FRIST,
  )

  it(
    'wechselt den PIN, ohne den Bestand anzufassen',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      vi.clearAllMocks()

      await useMessengerSperre.getState().pinAendern(PIN, 'neuer-pin-456')

      // Der Inhaltsschlüssel bleibt derselbe, nur sein Umschlag wird neu
      // geschrieben. Würde hier der Bestand angefasst, dauerte ein Wechsel bei
      // gewachsenem Verlauf Minuten statt eines Augenblicks.
      expect(umstellung.nachrichten).not.toHaveBeenCalled()
      expect(umstellung.ratchet).not.toHaveBeenCalled()

      useMessengerSperre.getState().sperren()
      expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(false)
      expect(await useMessengerSperre.getState().entsperren('neuer-pin-456')).toBe(true)
    },
    FRIST,
  )

  it(
    'lässt einen Wechsel mit falschem alten PIN nicht zu',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      await expect(
        useMessengerSperre.getState().pinAendern('war-es-nicht', 'neuer-pin-456'),
      ).rejects.toThrow(/stimmt nicht/)

      useMessengerSperre.getState().sperren()
      expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(true)
    },
    FRIST,
  )

  it(
    'schaltet ab und legt den Bestand wieder offen',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      vi.clearAllMocks()

      await useMessengerSperre.getState().abschalten(PIN)

      expect(useMessengerSperre.getState().eingerichtet).toBe(false)
      expect(siegelAktiv()).toBe(false)
      // Derselbe Durchlauf wie beim Einschalten, nur mit umgelegtem Schalter.
      expect(umstellung.nachrichten).toHaveBeenCalledOnce()
      expect(umstellung.geraet).toHaveBeenCalledOnce()
      expect(umstellung.ratchet).toHaveBeenCalledOnce()
      expect(umstellung.gruppen).toHaveBeenCalledOnce()
      expect(istOffen()).toBe(true)
    },
    FRIST,
  )

  it(
    'bleibt zu, wenn die Umstellung beim Abschalten abbricht',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      umstellung.ratchet.mockRejectedValueOnce(new Error('Platte voll'))

      await expect(useMessengerSperre.getState().abschalten(PIN)).rejects.toThrow()

      // Der Schalter muss zurück auf zu. Bliebe er offen, hielte nach dem
      // nächsten Neuladen niemand mehr die noch versiegelten Zeilen für
      // versiegelt — und der Schlüssel dazu wäre weg.
      expect(siegelAktiv()).toBe(true)
      expect(useMessengerSperre.getState().eingerichtet).toBe(true)

      // Ein zweiter Versuch räumt auf.
      await useMessengerSperre.getState().abschalten(PIN)
      expect(siegelAktiv()).toBe(false)
      expect(useMessengerSperre.getState().eingerichtet).toBe(false)
    },
    FRIST,
  )

  it(
    'schaltet mit falschem PIN nicht ab',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      await expect(useMessengerSperre.getState().abschalten('war-es-nicht')).rejects.toThrow()
      expect(siegelAktiv()).toBe(true)
      expect(useMessengerSperre.getState().eingerichtet).toBe(true)
    },
    FRIST,
  )

  it(
    'lässt nach genug Fehlversuchen warten, ohne etwas zu löschen',
    async () => {
      await useMessengerSperre.getState().einrichten(PIN)
      useMessengerSperre.getState().sperren()

      for (let i = 0; i < 5; i++) {
        useMessengerSperre.setState({ gesperrtBis: 0 })
        await useMessengerSperre.getState().entsperren('falsch-falsch')
      }

      expect(useMessengerSperre.getState().fehlversuche).toBe(5)
      expect(useMessengerSperre.getState().gesperrtBis).toBeGreaterThan(Date.now())

      // Die Wartezeit hält auch den richtigen PIN auf — aber nichts ist weg.
      expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(false)
      useMessengerSperre.setState({ gesperrtBis: 0 })
      expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(true)
    },
    FRIST,
  )

  describe('mit Gerätebindung', () => {
    const GEHEIMNIS = new Uint8Array(32).fill(7)

    beforeEach(() => {
      fach.schluesselfachVerfuegbar.mockResolvedValue(true)
      fach.geraeteGeheimnis.mockResolvedValue(GEHEIMNIS)
    })

    it(
      'bindet den Schlüssel an das Gerät und meldet einen verlorenen Speicher als solchen',
      async () => {
        await useMessengerSperre.getState().einrichten(PIN)
        expect(useMessengerSperre.getState().geraetebindung).toBe(true)

        useMessengerSperre.getState().sperren()

        // Der Schlüsselspeicher ist weg — Windows neu aufgesetzt, Profil
        // zurückgesetzt. Das ist kein Tippfehler, und die Meldung darf nicht
        // „Falscher PIN" lauten: wer das liest, tippt bis ans Ende seiner Tage.
        fach.geraeteGeheimnis.mockResolvedValue(null)
        expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(false)
        expect(useMessengerSperre.getState().fehler).toMatch(/Schlüsselspeicher/)
        expect(useMessengerSperre.getState().fehlversuche).toBe(0)

        fach.geraeteGeheimnis.mockResolvedValue(GEHEIMNIS)
        expect(await useMessengerSperre.getState().entsperren(PIN)).toBe(true)
      },
      FRIST,
    )

    it(
      'erzeugt beim Entsperren kein neues Gerätegeheimnis',
      async () => {
        await useMessengerSperre.getState().einrichten(PIN)
        expect(fach.geraeteGeheimnis).toHaveBeenCalledWith(true)

        fach.geraeteGeheimnis.mockClear()
        useMessengerSperre.getState().sperren()
        await useMessengerSperre.getState().entsperren(PIN)

        // Ein frisch erfundenes Geheimnis ergäbe einen anderen Schlüssel und
        // damit einen Umschlag, der nie wieder aufgeht.
        expect(fach.geraeteGeheimnis).toHaveBeenCalledWith(false)
        expect(fach.geraeteGeheimnis).not.toHaveBeenCalledWith(true)
      },
      FRIST,
    )
  })
})
