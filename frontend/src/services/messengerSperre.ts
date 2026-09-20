/**
 * Der Messenger-PIN.
 *
 * Der Messenger ist Ende-zu-Ende verschlüsselt, der Server sieht nur Umschläge.
 * Auf dem Gerät lag bis 09/2026 alles offen: der entschlüsselte Verlauf, der
 * private Geräteausweis, die Ratchet-Sitzungen, die Gruppenschlüssel. Wer an
 * das Profil kam, las mit. Dieser Store ist das Schloss davor.
 *
 * ## Zwei Stufen, ein Umschlag
 *
 * Der PIN verschlüsselt die Daten nicht selbst. Er leitet über Argon2id einen
 * Schlüssel ab, und der öffnet einen **Umschlag**, in dem der eigentliche
 * Inhaltsschlüssel liegt. Das kostet einen Schritt mehr und spart alles andere:
 * einen PIN zu ändern heißt, den Umschlag neu zu schreiben. Der Inhaltsschlüssel
 * bleibt derselbe, und kein einziger Datensatz muss angefasst werden. Bei einem
 * gewachsenen Verlauf ist das der Unterschied zwischen einem Augenblick und
 * einer Viertelstunde, in der niemand den Reiter schließen darf.
 *
 * ## Der Prüfstein ist der Umschlag selbst
 *
 * Es gibt keinen zweiten Wert, gegen den ein PIN geprüft wird. Ein falscher PIN
 * ergibt einen falschen Schlüssel, und der lässt den AES-GCM-Tag des Umschlags
 * scheitern. Das ist die Prüfung. Ein eigener Prüfwert wäre eine zweite
 * Wahrheit, die irgendwann von der ersten abweicht.
 *
 * ## Was ein verlorener Schlüsselspeicher bedeutet
 *
 * Mit Gerätebindung geht in die Ableitung ein Geheimnis aus dem Credential
 * Store ein. Eine kopierte Festplatte ist damit auf einem fremden Rechner
 * wertlos. Der Preis steht in derselben Zeile: ist der Credential Store weg —
 * Windows neu aufgesetzt, Profil zurückgesetzt —, ist der lokale Verlauf weg.
 * Wegen des Double Ratchet stehen die eigenen gesendeten Nachrichten nirgendwo
 * sonst, auch nicht auf dem Server. Deshalb sagt die Oberfläche das beim
 * Einschalten, und deshalb löscht auch kein Fehlversuch etwas: Wartezeit ja,
 * Verlust nein.
 */

import { generateSalt, deriveRawKey } from '@msdis/shield/kdf'
import {
  createWrappedUserKey,
  rotateWrappedKey,
  unwrapUserKey,
} from '@msdis/shield/key-management'
import { create } from 'zustand'
import i18n from '@/i18n'

import { leereSuchspeicher } from './verlaufSuche'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

import {
  type AutoSperrQuelle,
  fristAbgelaufen,
  liesFensterwechsel,
  liesSperrfrist,
  schreibeFensterwechsel,
  schreibeSperrfrist,
} from './autoSperre'
import { clearGeraeteMemory, schreibeGeraetBestandNeu } from './e2eeGeraet'
import {
  biometrieMoeglich,
  geraeteGeheimnis,
  rufePinAb,
  schluesselfachVerfuegbar,
  vergissGeraeteGeheimnis,
  vergissPin,
  verwahrePin,
} from './geraeteSchluesselfach'
import { schreibeGruppenBestandNeu } from './gruppenSchluessel'
import {
  setzeInhaltsSchluessel,
  setzeSiegelAktiv,
  setzeVerschlussMelder,
  siegelAktiv,
} from './lokaleVersiegelung'
import { schreibeNachrichtenBestandNeu } from './messengerLocalStore'
import { schreibeRatchetBestandNeu } from './ratchetSpeicher'

/**
 * Sechs Zeichen, Buchstaben erlaubt.
 *
 * Vier Ziffern wären eine Million Möglichkeiten und damit auch mit Argon2id in
 * überschaubarer Zeit durchprobiert, wenn jemand die Platte hat. Sechs sind
 * nicht viel mehr, aber der Sprung ist der, den ein Mensch noch mitgeht. Wer
 * mehr will, gibt mehr ein — nach oben steht nichts im Weg.
 */
export const PIN_MINDESTLAENGE = 6

/** Die Domäne der Ableitung. Steht im Format, darf sich nie ändern. */
const HKDF_INFO = 'msm-messenger-pin-v1'

/**
 * Der Namensraum der Sperrfrist-Einstellungen.
 *
 * Bewusst **nicht** kontobezogen: es ist eine Vorliebe, kein Geheimnis, und ein
 * Rechner, an dem jemand seinen Messenger nach fünf Minuten zugehen lässt, ist
 * derselbe Rechner für das zweite Konto.
 */
const SPERR_PRAEFIX = 'mss:messenger'

/**
 * Wartezeit nach Fehlversuchen: ab dem fünften, dann verdoppelnd bis fünf
 * Minuten. Gegen jemanden, der am offenen Gerät herumprobiert. Gegen jemanden
 * mit der Platte hilft das nichts — dagegen hilft die Länge des PIN und die
 * Gerätebindung, und so steht es auch in der Dokumentation.
 */
const WARTEN_AB_VERSUCH = 5
const WARTEN_GRUND_MS = 5_000
const WARTEN_DECKEL_MS = 5 * 60_000

function wartezeitFuer(fehlversuche: number): number {
  if (fehlversuche < WARTEN_AB_VERSUCH) return 0
  const stufen = fehlversuche - WARTEN_AB_VERSUCH
  return Math.min(WARTEN_GRUND_MS * 2 ** stufen, WARTEN_DECKEL_MS)
}

// ==========================================
// Ablage
// ==========================================

function schluessel(name: string): string | null {
  const konto = angemeldetesKonto()
  return konto === null ? null : `mss:messenger_${name}:konto:${konto}`
}

function lies(name: string): string | null {
  try {
    const k = schluessel(name)
    return k === null || typeof localStorage === 'undefined' ? null : localStorage.getItem(k)
  } catch {
    return null
  }
}

function schreibe(name: string, wert: string | null): void {
  try {
    const k = schluessel(name)
    if (k === null || typeof localStorage === 'undefined') return
    if (wert === null) localStorage.removeItem(k)
    else localStorage.setItem(k, wert)
  } catch {
    /* Ein privates Fenster ohne Ablage darf den Messenger nicht lahmlegen. */
  }
}

/**
 * Der gewickelte Inhaltsschlüssel.
 *
 * Er darf hier liegen: ohne den PIN ist er ein Haufen Bytes. Das ist der
 * Unterschied zu dem, was SEC-CRIT-01 aus dem Tresor entfernt hat — dort lag
 * ein Geheimnis, das sich mit einem zweiten Wert aus **derselben** Ablage
 * öffnen ließ. Wer den PIN nicht kennt, kommt hier nicht weiter.
 */
const UMSCHLAG = 'pin_umschlag'
const SALZ = 'pin_salz'
const BINDUNG = 'pin_bindung'
const BIOMETRIE = 'pin_biometrie'

// ==========================================
// Ableitung
// ==========================================

/**
 * PIN und, wo möglich, das Gerätegeheimnis zu Schlüsselbytes.
 *
 * `erzeugeBindung` steht beim Einrichten auf `true` und sonst auf `false`.
 * Beim Entsperren ein fehlendes Gerätegeheimnis neu zu erfinden, ergäbe einen
 * anderen Schlüssel und damit einen Umschlag, der nicht mehr aufgeht — der
 * Fehler sähe aus wie ein falscher PIN und wäre in Wahrheit ein verlorener
 * Schlüsselspeicher. Zwei verschiedene Dinge dürfen nicht dieselbe Meldung
 * bekommen.
 */
async function leiteAb(
  pin: string,
  salzBase64: string,
  mitBindung: boolean,
  erzeugeBindung: boolean,
): Promise<Uint8Array> {
  if (!mitBindung) return await deriveRawKey(pin, salzBase64)

  const bindung = await geraeteGeheimnis(erzeugeBindung)
  if (!bindung) {
    throw new Error(
      i18n.t('profile.messengerLock.errors.storageUnreachable'),
    )
  }
  return await deriveRawKey(pin, salzBase64, {
    strengthen: { hkdfSalt: bindung, info: HKDF_INFO },
  })
}

/** Nullt Schlüsselbytes, sobald sie nicht mehr gebraucht werden. */
function wische(bytes: Uint8Array | null): void {
  bytes?.fill(0)
}

/**
 * Schreibt alle vier Ablagen einmal neu.
 *
 * Derselbe Durchlauf für beide Richtungen: `versiegleZeile` richtet sich nach
 * dem Schalter, der vorher gesetzt wurde. Einschalten heißt Schalter an und
 * einmal durchlaufen, Ausschalten heißt Schalter aus und einmal durchlaufen.
 */
async function stelleBestandUm(kontoId: number): Promise<void> {
  await schreibeNachrichtenBestandNeu()
  await schreibeGeraetBestandNeu(kontoId)
  await schreibeRatchetBestandNeu()
  await schreibeGruppenBestandNeu()
}

// ==========================================
// Store
// ==========================================

export interface MessengerSperrZustand {
  /** Ist für dieses Konto auf diesem Gerät ein PIN eingerichtet? */
  eingerichtet: boolean
  entsperrt: boolean
  /** Ein Vorgang läuft — Argon2id braucht auf schwachen Geräten spürbar Zeit. */
  laeuft: boolean
  fehler: string | null
  fehlversuche: number
  /** Zeitstempel, bis zu dem kein weiterer Versuch angenommen wird. */
  gesperrtBis: number
  /** Kann diese Plattform einen Fingerabdruck **und** ein Geheimnis? */
  biometrieMoeglich: boolean
  biometrieAktiv: boolean
  /** Hängt der Schlüssel zusätzlich am Schlüsselspeicher dieses Geräts? */
  geraetebindung: boolean

  /** Minuten bis zur automatischen Sperre. `0` heißt nie. */
  sperrfrist: number
  sperrtBeiFensterwechsel: boolean
  letzteAktivitaet: number

  setzeSperrfrist: (minuten: number) => void
  setzeFensterwechsel: (an: boolean) => void
  merkeAktivitaet: () => void
  pruefeFrist: () => void

  initialisiere: () => Promise<void>
  einrichten: (pin: string) => Promise<void>
  entsperren: (pin: string) => Promise<boolean>
  entsperrenMitBiometrie: () => Promise<boolean>
  sperren: () => void
  pinAendern: (alt: string, neu: string) => Promise<void>
  abschalten: (pin: string) => Promise<void>
  biometrieEinschalten: (pin: string) => Promise<void>
  biometrieAusschalten: () => Promise<void>
  fehlerLoeschen: () => void
}

export const useMessengerSperre = create<MessengerSperrZustand>((set, get) => ({
  eingerichtet: false,
  entsperrt: false,
  laeuft: false,
  fehler: null,
  fehlversuche: 0,
  gesperrtBis: 0,
  biometrieMoeglich: false,
  biometrieAktiv: false,
  geraetebindung: false,

  // 15 Minuten wie beim Tresor. Sofort wäre für einen Messenger, den man
  // nebenbei offen hat, eine Zumutung; nie wäre kein Schutz.
  sperrfrist: liesSperrfrist(SPERR_PRAEFIX, 15),
  sperrtBeiFensterwechsel: liesFensterwechsel(SPERR_PRAEFIX),
  letzteAktivitaet: Date.now(),

  setzeSperrfrist: (minuten: number) => {
    schreibeSperrfrist(SPERR_PRAEFIX, minuten)
    set({ sperrfrist: minuten, letzteAktivitaet: Date.now() })
  },

  setzeFensterwechsel: (an: boolean) => {
    schreibeFensterwechsel(SPERR_PRAEFIX, an)
    set({ sperrtBeiFensterwechsel: an })
  },

  /**
   * Merkt Aktivität — und sperrt, wenn die Frist längst abgelaufen war.
   *
   * Die zweite Hälfte ist der Grund, warum das nicht nur ein Zeitstempel ist:
   * ein schlafender Rechner hält keine Zeitgeber am Laufen. Wer den Deckel nach
   * zwei Stunden aufklappt, erzeugt als erstes ein `mousemove` — und ohne diese
   * Prüfung wäre das die Aktivität, die die abgelaufene Frist zurücksetzt.
   */
  merkeAktivitaet: () => {
    const { entsperrt, sperrfrist, letzteAktivitaet } = get()
    if (entsperrt && fristAbgelaufen(letzteAktivitaet, sperrfrist)) {
      get().sperren()
      return
    }
    set({ letzteAktivitaet: Date.now() })
  },

  pruefeFrist: () => {
    const { entsperrt, sperrfrist, letzteAktivitaet } = get()
    if (entsperrt && fristAbgelaufen(letzteAktivitaet, sperrfrist)) get().sperren()
  },

  /**
   * Liest den Stand für das angemeldete Konto. Muss laufen, sobald das Konto
   * feststeht — vorher weiß niemand, wessen PIN gemeint ist.
   */
  initialisiere: async () => {
    const aktiv = siegelAktiv()
    set({
      eingerichtet: aktiv && lies(UMSCHLAG) !== null,
      // Ein frisch geladener Reiter ist gesperrt. Es gibt keinen Zustand, in
      // dem ein Siegel aktiv ist und der Schlüssel schon im Speicher liegt.
      entsperrt: !aktiv,
      geraetebindung: lies(BINDUNG) === 'true',
      biometrieAktiv: lies(BIOMETRIE) === 'true',
    })
    set({ biometrieMoeglich: await biometrieMoeglich() })
  },

  einrichten: async (pin: string) => {
    const kontoId = angemeldetesKonto()
    if (kontoId === null) throw new Error(i18n.t('profile.messengerLock.errors.noAccount'))
    if (pin.length < PIN_MINDESTLAENGE) {
      throw new Error(i18n.t('profile.messengerLock.errors.pinTooShort', { count: PIN_MINDESTLAENGE }))
    }
    if (get().eingerichtet) throw new Error(i18n.t('profile.messengerLock.errors.alreadySetUp'))

    set({ laeuft: true, fehler: null })
    let kdfBytes: Uint8Array | null = null
    try {
      const bindungMoeglich = await schluesselfachVerfuegbar()
      const salz = generateSalt()
      kdfBytes = await leiteAb(pin, salz, bindungMoeglich, true)
      const { encryptedUserKey, userKey } = await createWrappedUserKey(kdfBytes)

      // Erst den Schlüssel scharf schalten, dann den Schalter umlegen, dann
      // umstellen. Andersherum liefe der Durchlauf gegen ein aktives Siegel
      // ohne Schlüssel und bräche bei der ersten Zeile ab.
      setzeInhaltsSchluessel(userKey)
      schreibe(SALZ, salz)
      schreibe(UMSCHLAG, encryptedUserKey)
      schreibe(BINDUNG, bindungMoeglich ? 'true' : 'false')
      setzeSiegelAktiv(true)

      await stelleBestandUm(kontoId)

      set({
        eingerichtet: true,
        entsperrt: true,
        letzteAktivitaet: Date.now(),
        geraetebindung: bindungMoeglich,
        fehlversuche: 0,
        gesperrtBis: 0,
      })
    } catch (err) {
      // Zurück auf offen: ein halb eingerichteter PIN wäre ein Messenger, der
      // sich nicht mehr öffnen lässt.
      setzeSiegelAktiv(false)
      schreibe(UMSCHLAG, null)
      schreibe(SALZ, null)
      schreibe(BINDUNG, null)
      setzeInhaltsSchluessel(null)
      set({ eingerichtet: false, entsperrt: true })
      throw err
    } finally {
      wische(kdfBytes)
      set({ laeuft: false })
    }
  },

  entsperren: async (pin: string) => {
    const warten = get().gesperrtBis - Date.now()
    if (warten > 0) {
      set({
        fehler: i18n.t('profile.messengerLock.waiting', { count: Math.ceil(warten / 1000) }),
      })
      return false
    }

    const umschlag = lies(UMSCHLAG)
    const salz = lies(SALZ)
    if (!umschlag || !salz) {
      set({ fehler: i18n.t('profile.messengerLock.errors.noPinOnDevice') })
      return false
    }

    set({ laeuft: true, fehler: null })
    let kdfBytes: Uint8Array | null = null
    try {
      kdfBytes = await leiteAb(pin, salz, lies(BINDUNG) === 'true', false)
      const userKey = await unwrapUserKey(umschlag, kdfBytes)
      setzeInhaltsSchluessel(userKey)
      // Entsperren **ist** Aktivität. Ohne diese Zeile läuft die Frist weiter,
      // während der Sperrschirm steht: wer die App aufmacht, eine Viertelstunde
      // woanders hinschaut und dann seinen PIN eingibt, ist im Moment des
      // Entsperrens schon „seit 15 Minuten untätig" — der Messenger blitzt auf
      // und ist beim nächsten Takt wieder zu.
      set({
        entsperrt: true,
        letzteAktivitaet: Date.now(),
        fehlversuche: 0,
        gesperrtBis: 0,
        fehler: null,
      })
      return true
    } catch (err) {
      // Ein fehlender Schlüsselspeicher ist kein falscher PIN. `leiteAb` wirft
      // dafür eine eigene Meldung, und die muss durchkommen: wer hier „PIN
      // falsch" liest, tippt bis ans Ende seiner Tage.
      const speicherWeg =
        err instanceof Error &&
        (err.message.includes('Schlüsselspeicher') ||
          err.message === i18n.t('profile.messengerLock.errors.storageUnreachable'))
      const fehlversuche = speicherWeg ? get().fehlversuche : get().fehlversuche + 1
      const warteMs = wartezeitFuer(fehlversuche)
      set({
        fehlversuche,
        gesperrtBis: warteMs > 0 ? Date.now() + warteMs : 0,
        fehler: speicherWeg
          ? (err as Error).message
          : i18n.t('profile.messengerLock.errors.wrongPin'),
      })
      return false
    } finally {
      wische(kdfBytes)
      set({ laeuft: false })
    }
  },

  entsperrenMitBiometrie: async () => {
    if (!get().biometrieAktiv) return false
    set({ laeuft: true, fehler: null })
    try {
      const pin = await rufePinAb()
      if (!pin) {
        set({ fehler: i18n.t('profile.messengerLock.errors.bioFailed') })
        return false
      }
      set({ laeuft: false })
      return await get().entsperren(pin)
    } finally {
      set({ laeuft: false })
    }
  },

  sperren: () => {
    if (!siegelAktiv()) return
    setzeInhaltsSchluessel(null)
    // Der Geräteausweis liegt nach dem ersten Zugriff auch im Arbeitsspeicher.
    // Bliebe er dort, liefe der Messenger nach der automatischen Sperre
    // fröhlich weiter: er hätte alles, was er zum Entschlüsseln braucht, und
    // die Sperre wäre ein Vorhang.
    clearGeraeteMemory()
    // Die Suche hält entsiegelte Verläufe im Arbeitsspeicher. Blieben sie
    // liegen, ließe sich nach der Sperre weiter darin suchen.
    leereSuchspeicher()
    set({ entsperrt: false, fehler: null })
  },

  pinAendern: async (alt: string, neu: string) => {
    if (neu.length < PIN_MINDESTLAENGE) {
      throw new Error(i18n.t('profile.messengerLock.errors.pinTooShort', { count: PIN_MINDESTLAENGE }))
    }
    const umschlag = lies(UMSCHLAG)
    const salz = lies(SALZ)
    if (!umschlag || !salz) throw new Error(i18n.t('profile.messengerLock.errors.noPinOnDevice'))

    set({ laeuft: true, fehler: null })
    let alteBytes: Uint8Array | null = null
    let neueBytes: Uint8Array | null = null
    try {
      const mitBindung = lies(BINDUNG) === 'true'
      alteBytes = await leiteAb(alt, salz, mitBindung, false)
      // Prüft den alten PIN: geht der Umschlag nicht auf, wirft das hier.
      await unwrapUserKey(umschlag, alteBytes)

      // Neues Salz zum neuen PIN. Dasselbe Salz weiterzuverwenden verriete,
      // dass der PIN gewechselt hat, aber nicht mehr — und kostet nichts.
      const neuesSalz = generateSalt()
      neueBytes = await leiteAb(neu, neuesSalz, mitBindung, false)
      const neuerUmschlag = await rotateWrappedKey(umschlag, alteBytes, neueBytes)

      schreibe(SALZ, neuesSalz)
      schreibe(UMSCHLAG, neuerUmschlag)

      // Der hinterlegte PIN wäre sonst der alte — und der öffnet nichts mehr.
      if (get().biometrieAktiv) await verwahrePin(neu)
    } catch (err) {
      throw err instanceof Error &&
        (err.message.includes('Schlüsselspeicher') ||
          err.message === i18n.t('profile.messengerLock.errors.storageUnreachable'))
        ? err
        : new Error(i18n.t('profile.messengerLock.errors.currentPinWrong'))
    } finally {
      wische(alteBytes)
      wische(neueBytes)
      set({ laeuft: false })
    }
  },

  abschalten: async (pin: string) => {
    const kontoId = angemeldetesKonto()
    if (kontoId === null) throw new Error(i18n.t('profile.messengerLock.errors.noAccount'))

    // Erst entsperren: ohne Schlüssel ließe sich der Bestand nicht öffnen, und
    // der Durchlauf schriebe leere Zeilen über den Verlauf.
    if (!get().entsperrt) {
      const offen = await get().entsperren(pin)
      if (!offen) throw new Error(i18n.t('profile.messengerLock.errors.wrongPin'))
    } else {
      const umschlag = lies(UMSCHLAG)
      const salz = lies(SALZ)
      if (!umschlag || !salz) throw new Error(i18n.t('profile.messengerLock.errors.noPinOnDevice'))
      let bytes: Uint8Array | null = null
      try {
        bytes = await leiteAb(pin, salz, lies(BINDUNG) === 'true', false)
        await unwrapUserKey(umschlag, bytes)
      } catch {
        throw new Error(i18n.t('profile.messengerLock.errors.wrongPin'))
      } finally {
        wische(bytes)
      }
    }

    set({ laeuft: true, fehler: null })
    try {
      // Schalter aus, Schlüssel bleibt: der Durchlauf muss die versiegelten
      // Zeilen noch öffnen können, während er sie im Klartext zurückschreibt.
      setzeSiegelAktiv(false)
      try {
        await stelleBestandUm(kontoId)
      } catch (err) {
        // Bricht der Durchlauf in der Mitte ab, liegen noch versiegelte Zeilen
        // da. Bliebe der Schalter aus, wären die nach dem nächsten Neuladen
        // verloren: der Schlüssel wäre aus dem Speicher, der Umschlag gelöscht,
        // und niemand hielte sie noch für versiegelt. Also zurück auf zu — die
        // bereits umgeschriebenen Zeilen bleiben lesbar, der Rest auch, und ein
        // zweiter Versuch räumt auf.
        setzeSiegelAktiv(true)
        throw err
      }

      await get().biometrieAusschalten()
      await vergissGeraeteGeheimnis()
      schreibe(UMSCHLAG, null)
      schreibe(SALZ, null)
      schreibe(BINDUNG, null)
      setzeInhaltsSchluessel(null)
      set({
        eingerichtet: false,
        entsperrt: true,
        geraetebindung: false,
        fehlversuche: 0,
        gesperrtBis: 0,
      })
    } finally {
      set({ laeuft: false })
    }
  },

  biometrieEinschalten: async (pin: string) => {
    if (!get().eingerichtet) throw new Error(i18n.t('profile.messengerLock.errors.setupFirst'))
    const umschlag = lies(UMSCHLAG)
    const salz = lies(SALZ)
    if (!umschlag || !salz) throw new Error(i18n.t('profile.messengerLock.errors.noPinOnDevice'))

    set({ laeuft: true, fehler: null })
    let bytes: Uint8Array | null = null
    try {
      // Nur ein PIN, der wirklich öffnet, darf ins Fach. Sonst hinterlegt
      // jemand einen Tippfehler und merkt es erst, wenn der Finger nicht mehr
      // hilft.
      bytes = await leiteAb(pin, salz, lies(BINDUNG) === 'true', false)
      await unwrapUserKey(umschlag, bytes)

      await verwahrePin(pin)
      schreibe(BIOMETRIE, 'true')
      set({ biometrieAktiv: true })
    } catch (err) {
      throw err instanceof Error ? err : new Error(i18n.t('profile.messengerLock.errors.pinIncorrect'))
    } finally {
      wische(bytes)
      set({ laeuft: false })
    }
  },

  biometrieAusschalten: async () => {
    await vergissPin()
    schreibe(BIOMETRIE, null)
    set({ biometrieAktiv: false })
  },

  fehlerLoeschen: () => set({ fehler: null }),
}))

/**
 * Wenn eine Ablage an eine verschlossene Zeile gerät, stimmt die Anzeige nicht
 * mehr mit der Wirklichkeit überein — jemand hält den Messenger für offen und
 * bekommt leere Ergebnisse. Das hier holt die Anzeige zurück.
 */
setzeVerschlussMelder(() => {
  const { entsperrt, sperren } = useMessengerSperre.getState()
  if (entsperrt) sperren()
})

/**
 * Was `useAutoSperre` braucht, um den Messenger zu bewachen.
 *
 * Eine feste Größe und kein bei jedem Rendern neu gebautes Objekt: sie steht im
 * Abhängigkeitsfeld des Hakens, und ein wechselndes Objekt meldete bei jedem
 * Durchlauf alle Ereignisse ab und wieder an.
 */
export const messengerAutoSperrQuelle: AutoSperrQuelle = {
  istEntsperrt: () => useMessengerSperre.getState().entsperrt,
  istBeschaeftigt: () => useMessengerSperre.getState().laeuft,
  sperrtBeiFensterwechsel: () => useMessengerSperre.getState().sperrtBeiFensterwechsel,
  merkeAktivitaet: () => useMessengerSperre.getState().merkeAktivitaet(),
  pruefeFrist: () => useMessengerSperre.getState().pruefeFrist(),
  sperre: () => useMessengerSperre.getState().sperren(),
}
