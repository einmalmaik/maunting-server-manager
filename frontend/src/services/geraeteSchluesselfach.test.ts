/**
 * @vitest-environment node
 *
 * Das Schlüsselfach des Betriebssystems.
 *
 * Die Brücke selbst ist hier gefälscht — was hinter `invoke` passiert, ist Sache
 * von Rust und Kotlin. Geprüft wird, was diese Datei entscheidet: wann gefragt
 * wird, wann ein Gerätegeheimnis entstehen darf und wann eben nicht.
 *
 * Der wichtigste Punkt steht in „erfindet beim Entsperren kein neues": ein frisch
 * erzeugtes Gerätegeheimnis ergäbe einen anderen Schlüssel als der, mit dem der
 * Umschlag zugemacht wurde. Der ginge dann nie wieder auf.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const bruecke = {
  FACH_MESSENGER: 'messenger_biometric_key',
  FACH_MESSENGER_GERAET: 'messenger_device_secret',
  biometrieEntsperren: vi.fn(async (_n?: string, _f?: string) => ''),
  biometrieLoeschen: vi.fn(async (_f?: string) => {}),
  biometrieSpeichern: vi.fn(async (_g: string, _f?: string) => {}),
  biometrieSpeicherFragtSelbst: vi.fn(async () => false),
  biometrieSpeicherVerfuegbar: vi.fn(async () => true),
  messengerGeraetegeheimnis: vi.fn(async () => null as string | null),
  pruefeBiometrieVerfuegbar: vi.fn(async () => true),
  verifiziereBiometrie: vi.fn(async (_n?: string) => true),
}
vi.mock('@/desktop/tauri', () => bruecke)

const fach = await import('./geraeteSchluesselfach')

describe('geraeteSchluesselfach', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    bruecke.biometrieSpeicherFragtSelbst.mockResolvedValue(false)
    bruecke.biometrieSpeicherVerfuegbar.mockResolvedValue(true)
    bruecke.pruefeBiometrieVerfuegbar.mockResolvedValue(true)
    bruecke.messengerGeraetegeheimnis.mockResolvedValue(null)
    bruecke.verifiziereBiometrie.mockResolvedValue(true)
  })

  describe('biometrieMoeglich', () => {
    it('verlangt beides: bestätigen können und verwahren können', async () => {
      expect(await fach.biometrieMoeglich()).toBe(true)

      // Fragen ohne Verwahren nützt niemandem — das war der Zustand auf Android
      // bis 09/2026, und der Tresor bot dort einen Schnelleinstieg an, der beim
      // Einrichten scheiterte.
      bruecke.biometrieSpeicherVerfuegbar.mockResolvedValue(false)
      expect(await fach.biometrieMoeglich()).toBe(false)

      bruecke.biometrieSpeicherVerfuegbar.mockResolvedValue(true)
      bruecke.pruefeBiometrieVerfuegbar.mockResolvedValue(false)
      expect(await fach.biometrieMoeglich()).toBe(false)
    })
  })

  describe('verwahrePin', () => {
    it('bestätigt vorher, wenn der Schlüsselspeicher es nicht selbst tut', async () => {
      await fach.verwahrePin('geheim-123')

      expect(bruecke.verifiziereBiometrie).toHaveBeenCalledOnce()
      expect(bruecke.biometrieSpeichern).toHaveBeenCalledWith(
        'geheim-123',
        'messenger_biometric_key',
      )
    })

    it('bestätigt nicht doppelt, wenn der Schlüsselspeicher selbst fragt', async () => {
      bruecke.biometrieSpeicherFragtSelbst.mockResolvedValue(true)

      await fach.verwahrePin('geheim-123')

      // Android: der Keystore gibt den Schlüssel erst nach dem Fingerabdruck
      // frei, schon zum Verschlüsseln. Eine Abfrage davor wäre derselbe Finger
      // zweimal hintereinander.
      expect(bruecke.verifiziereBiometrie).not.toHaveBeenCalled()
      expect(bruecke.biometrieSpeichern).toHaveBeenCalledOnce()
    })

    it('legt nichts ab, wenn die Bestätigung scheitert', async () => {
      bruecke.verifiziereBiometrie.mockResolvedValue(false)

      await expect(fach.verwahrePin('geheim-123')).rejects.toThrow()
      expect(bruecke.biometrieSpeichern).not.toHaveBeenCalled()
    })
  })

  describe('geraeteGeheimnis', () => {
    it('erfindet beim Entsperren kein neues', async () => {
      expect(await fach.geraeteGeheimnis(false)).toBeNull()

      // Ein frisch erzeugtes Geheimnis ergäbe einen anderen Schlüssel. Fehlt es
      // beim Entsperren, ist das eine Aussage — der Schlüsselspeicher ist weg —
      // und keine Einladung, eines zu erfinden.
      expect(bruecke.biometrieSpeichern).not.toHaveBeenCalled()
    })

    it('legt beim Einrichten eines an und gibt dieselben Bytes zurück', async () => {
      const frisch = await fach.geraeteGeheimnis(true)

      expect(frisch).toBeInstanceOf(Uint8Array)
      expect(frisch).toHaveLength(32)
      expect(bruecke.biometrieSpeichern).toHaveBeenCalledOnce()

      const [abgelegt, fachName] = bruecke.biometrieSpeichern.mock.calls[0]
      expect(fachName).toBe('messenger_device_secret')
      // Was abgelegt wurde, muss das sein, was der Aufrufer bekommt — sonst
      // rechnet die Ableitung mit dem einen und findet später das andere.
      const { base64ToBytes } = await import('@msdis/shield/core')
      expect(Array.from(base64ToBytes(abgelegt))).toEqual(Array.from(frisch!))
    })

    it('gibt das hinterlegte zurück, statt ein zweites anzulegen', async () => {
      const { bytesToBase64 } = await import('@msdis/shield/core')
      const vorhanden = new Uint8Array(32).fill(7)
      bruecke.messengerGeraetegeheimnis.mockResolvedValue(bytesToBase64(vorhanden))

      expect(Array.from((await fach.geraeteGeheimnis(true))!)).toEqual(Array.from(vorhanden))
      expect(bruecke.biometrieSpeichern).not.toHaveBeenCalled()
    })

    it('meldet null, wo es keinen Schlüsselspeicher gibt', async () => {
      // Der Browser. Ein Geheimnis im localStorage sähe aus wie Schutz und wäre
      // keiner (SEC-CRIT-01) — also gibt es hier keines.
      bruecke.biometrieSpeicherVerfuegbar.mockResolvedValue(false)

      expect(await fach.geraeteGeheimnis(true)).toBeNull()
      expect(bruecke.biometrieSpeichern).not.toHaveBeenCalled()
    })
  })
})
