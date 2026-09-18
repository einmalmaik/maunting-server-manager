/**
 * Was von `e2eeCrypto` übrig ist: Mailbox-Kennungen, der Hybridumschlag, die
 * Schlüsselablage und die beiden Prüffunktionen.
 *
 * Die Datei war doppelt so lang und prüfte fünf Verfahren. Drei davon gibt es
 * nicht mehr, zwei davon waren gar nicht Ende-zu-Ende verschlüsselt. Mit dem
 * Code sind auch ihre Tests weg — ein Test, der ein gelöschtes Verfahren
 * bescheinigt, hält nur die Erinnerung daran am Leben.
 *
 * Geblieben und neu ist die Gegenprobe am Ende: die alten Präfixe müssen
 * **abgewiesen** werden, auch beim Lesen.
 */

import { beforeAll, describe, expect, it } from 'vitest'
import {
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  generateLocalE2eeKeyPair,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
} from './e2eeCrypto'

/** Base64 aus Bytes, ohne Umweg über DIS — der Test baut damit Umschlag-Attrappen. */
function b64(bytes: Uint8Array): string {
  let s = ''
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s)
}

/** 32 Bytes, deren erste zwölf nicht null sind: sieht aus wie IV plus Tag. */
function plausiblerChiffretext(): string {
  const bytes = new Uint8Array(32)
  for (let i = 0; i < bytes.length; i++) bytes[i] = (i % 250) + 1
  return b64(bytes)
}

describe('e2eeCrypto (@msdis/shield Zero-Knowledge)', () => {
  describe('Mailbox-Kennungen', () => {
    it('benennt dasselbe Postfach, egal von welcher Seite gefragt wird', async () => {
      const box1 = await deriveBlindMailboxId(42, 99)
      const box2 = await deriveBlindMailboxId(99, 42)
      const boxOther = await deriveBlindMailboxId(42, 100)

      expect(box1).toBe(box2)
      expect(box1).toHaveLength(64) // SHA-256 hex
      expect(box1).not.toBe(boxOther)
    })

    it('gibt jeder Gruppe ihr eigenes Postfach', async () => {
      const a = await deriveGroupBlindMailboxId(7)
      const b = await deriveGroupBlindMailboxId(7)
      const c = await deriveGroupBlindMailboxId(8)

      expect(a).toBe(b)
      expect(a).toHaveLength(64)
      expect(a).not.toBe(c)
    })

    it('trägt keine Benutzerkennung sichtbar mit sich', async () => {
      // Die Kennung ist ein Hash. Sie muss auf beiden Geräten gleich
      // herauskommen, ohne dass jemand sie verabredet — aber sie darf dem
      // Server nicht verraten, wer hier miteinander spricht.
      const box = await deriveBlindMailboxId(1234, 5678)
      expect(box).not.toContain('1234')
      expect(box).not.toContain('5678')
      expect(box).toMatch(/^[0-9a-f]{64}$/)
    })
  })

  describe('Hybridumschlag (RSA-OAEP)', () => {
    let aliceKeys: { publicKeyJwk: string; privateKeyJwk: string }

    beforeAll(async () => {
      aliceKeys = await generateLocalE2eeKeyPair()
    }, 30_000)

    it('versiegelt gegen einen öffentlichen Schlüssel und öffnet mit dem privaten', async () => {
      expect(aliceKeys.publicKeyJwk).toContain('"kty":"RSA"')
      expect(aliceKeys.privateKeyJwk).toContain('"d":')

      const message = 'Hybrid E2EE payload for public key recipient'
      const envelope = await encryptE2eeHybrid(message, aliceKeys.publicKeyJwk)
      expect(envelope.startsWith('sv-e2ee-hybrid-v1:')).toBe(true)

      const decrypted = await decryptE2eeHybrid(envelope, aliceKeys.privateKeyJwk)
      expect(decrypted).toBe(message)
    }, 30_000)

    it('bleibt für einen fremden privaten Schlüssel zu', async () => {
      const bobKeys = await generateLocalE2eeKeyPair()

      const envelope = await encryptE2eeHybrid('For Alice eyes only', aliceKeys.publicKeyJwk)
      await expect(decryptE2eeHybrid(envelope, bobKeys.privateKeyJwk)).rejects.toThrow()
    }, 30_000)

    it('lässt sich doppelt versiegeln, für Empfänger und Absender', async () => {
      // Das trägt den ganzen Messenger: der Absender muss seine eigene
      // Zustellung des Gruppen- oder Raumschlüssels wieder öffnen können.
      const bobKeys = await generateLocalE2eeKeyPair()
      const message = 'Nachricht zwischen Alice und Bob'

      const envelope = await encryptE2eeHybrid(message, bobKeys.publicKeyJwk, aliceKeys.publicKeyJwk)
      expect(envelope.startsWith('sv-e2ee-hybrid-v1:')).toBe(true)

      expect(await decryptE2eeHybrid(envelope, bobKeys.privateKeyJwk)).toBe(message)
      expect(await decryptE2eeHybrid(envelope, aliceKeys.privateKeyJwk)).toBe(message)
    }, 30_000)

    it('öffnet Altumschläge, deren Schlüssel als JSON-Array verpackt war', async () => {
      const { rsaOaepEncrypt, importRsaOaepPublicKey } = await import('@msdis/shield/asymmetric')
      const { formatEnvelope } = await import('@msdis/shield/format-versioning')
      const { encryptString, importAesGcmRawKey } = await import('@msdis/shield/aead')
      const { E2EE_HYBRID_ENVELOPE_SPEC } = await import('./e2eeCrypto')

      const symBytes = new Uint8Array(32)
      crypto.getRandomValues(symBytes)
      const symKey = await importAesGcmRawKey(symBytes, ['encrypt'])
      const ciphertext = await encryptString('Legacy Format Test', symKey, 'msm:hybrid:aad')

      const legacyKeyString = JSON.stringify(Array.from(symBytes))
      const pubKey = await importRsaOaepPublicKey(JSON.parse(aliceKeys.publicKeyJwk))
      const wrappedKey = await rsaOaepEncrypt(legacyKeyString, pubKey)

      const legacyEnvelope = formatEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, `${wrappedKey}.${ciphertext}`)

      expect(await decryptE2eeHybrid(legacyEnvelope, aliceKeys.privateKeyJwk)).toBe('Legacy Format Test')
    }, 30_000)
  })

  describe('Schlüsselablage', () => {
    it('legt den Schlüssel ab, ohne ihn in den localStorage zu schreiben', async () => {
      const { storeLocalKeyPair, getLocalKeyPair } = await import('./e2eeCrypto')
      const userId = 777

      const keyPair = await generateLocalE2eeKeyPair()
      await storeLocalKeyPair(userId, keyPair)

      expect(localStorage.getItem(`msm_e2ee_identity_${userId}_priv`)).toBeNull()

      const retrieved = await getLocalKeyPair(userId)
      expect(retrieved?.publicKeyJwk).toBe(keyPair.publicKeyJwk)
      expect(retrieved?.privateKeyJwk).toBe(keyPair.privateKeyJwk)
    }, 30_000)

    it('räumt den Speicher beim Abmelden', async () => {
      const { storeLocalKeyPair, clearMemoryKeyStore, getLocalKeyPair } = await import('./e2eeCrypto')
      const userId = 888
      await storeLocalKeyPair(userId, {
        publicKeyJwk: '{"mock": true}',
        privateKeyJwk: '{"priv": true}',
      })
      expect((await getLocalKeyPair(userId))?.privateKeyJwk).toBe('{"priv": true}')

      clearMemoryKeyStore()
      // Ohne IndexedDB unter jsdom bleibt nur der RAM-Speicher, und der ist leer.
      expect(await getLocalKeyPair(userId)).toBeNull()
    })

    it('entfernt Klartextreste und Schlüssel aus localStorage und sessionStorage', async () => {
      const { scrubPlaintextStorage } = await import('./e2eeCrypto')
      localStorage.setItem('msm:chat_cache:mailbox123', JSON.stringify([{ text: 'Leaked Plaintext' }]))
      localStorage.setItem('msm_e2ee_identity_42_priv', 'leaked-private-key')
      localStorage.setItem('msm_keep_this_unrelated_key', 'safe')

      sessionStorage.setItem('msm:chat_cache:session_leak', 'leak')
      sessionStorage.setItem('msm_e2ee_session_priv', 'secret')
      sessionStorage.setItem('keep_session_unrelated', 'ok')

      scrubPlaintextStorage()

      expect(localStorage.getItem('msm:chat_cache:mailbox123')).toBeNull()
      expect(localStorage.getItem('msm_e2ee_identity_42_priv')).toBeNull()
      expect(localStorage.getItem('msm_keep_this_unrelated_key')).toBe('safe')

      expect(sessionStorage.getItem('msm:chat_cache:session_leak')).toBeNull()
      expect(sessionStorage.getItem('msm_e2ee_session_priv')).toBeNull()
      expect(sessionStorage.getItem('keep_session_unrelated')).toBe('ok')

      localStorage.removeItem('msm_keep_this_unrelated_key')
      sessionStorage.removeItem('keep_session_unrelated')
    })
  })

  describe('Prüfung öffentlicher Schlüssel', () => {
    it('nimmt gültige RSA-OAEP-Schlüssel und weist kaputte Formate ab', async () => {
      const { validatePublicKeyJwk } = await import('./e2eeCrypto')
      const validKey = await generateLocalE2eeKeyPair()

      const resultValid = validatePublicKeyJwk(validKey.publicKeyJwk)
      expect(resultValid.valid).toBe(true)
      expect(resultValid.jwk?.kty).toBe('RSA')

      expect(validatePublicKeyJwk('not-json').valid).toBe(false)
      expect(validatePublicKeyJwk('').valid).toBe(false)
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'oct', k: 'secret' })).valid).toBe(false)
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'RSA', e: 'AQAB' })).valid).toBe(false)
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'RSA', n: 'abc' })).valid).toBe(false)

      // Der wichtigste Fall: ein privater Bestandteil im „öffentlichen" Schlüssel.
      const leakedPriv = JSON.stringify({
        kty: 'RSA',
        n: 'a'.repeat(350),
        e: 'AQAB',
        d: 'private-exponent-leak',
      })
      const resultLeak = validatePublicKeyJwk(leakedPriv)
      expect(resultLeak.valid).toBe(false)
      expect(resultLeak.error).toContain('Security violation')

      const leakedCrt = JSON.stringify({
        kty: 'RSA',
        n: 'a'.repeat(350),
        e: 'AQAB',
        dmp1: 'crt-leak',
      })
      expect(validatePublicKeyJwk(leakedCrt).valid).toBe(false)
      expect(validatePublicKeyJwk(leakedCrt).error).toContain('Security violation')

      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'RSA', n: 'too-short', e: 'AQAB' })).valid).toBe(false)
    }, 30_000)

    it('verschlüsselt nicht gegen einen ungültigen Schlüssel', async () => {
      await expect(encryptE2eeHybrid('secret message', 'invalid-key')).rejects.toThrow()
      await expect(
        encryptE2eeHybrid('secret message', JSON.stringify({ kty: 'oct', k: '1234' }))
      ).rejects.toThrow()
    })

    it('fällt nicht auf vertauschte Algorithmen herein', async () => {
      const { validatePublicKeyJwk } = await import('./e2eeCrypto')
      const validPair = await generateLocalE2eeKeyPair()
      const baseJwk = JSON.parse(validPair.publicKeyJwk)

      const resNone = validatePublicKeyJwk(JSON.stringify({ ...baseJwk, alg: 'none' }))
      expect(resNone.valid).toBe(false)
      expect(resNone.error).toContain('Security violation')

      const resRs256 = validatePublicKeyJwk(JSON.stringify({ ...baseJwk, alg: 'RS256' }))
      expect(resRs256.valid).toBe(false)
      expect(resRs256.error).toContain('Security violation')

      expect(validatePublicKeyJwk(JSON.stringify({ ...baseJwk, alg: 'HS256' })).valid).toBe(false)

      const resUse = validatePublicKeyJwk(JSON.stringify({ ...baseJwk, use: 'sig' }))
      expect(resUse.valid).toBe(false)
      expect(resUse.error).toContain('invalid key use')

      const resOps = validatePublicKeyJwk(JSON.stringify({ ...baseJwk, key_ops: ['sign', 'verify'] }))
      expect(resOps.valid).toBe(false)
      expect(resOps.error).toContain('signature operations forbidden')

      expect(validatePublicKeyJwk(JSON.stringify({ ...baseJwk, alg: 'RSA-OAEP', use: 'enc' })).valid).toBe(true)
    }, 30_000)
  })

  describe('Bindung der gepackten Schlüssel an den Chiffretext', () => {
    it('merkt, wenn jemand den gepackten Schlüssel austauscht', async () => {
      const alice = await generateLocalE2eeKeyPair()
      const bob = await generateLocalE2eeKeyPair()

      const originalEnvelope = await encryptE2eeHybrid(
        'Vertrauliche Finanzdaten',
        bob.publicKeyJwk,
        alice.publicKeyJwk
      )

      const prefix = 'sv-e2ee-hybrid-v1:'
      const payload = originalEnvelope.slice(prefix.length)
      const dotIdx = payload.indexOf('.')
      const wrappedKeys = payload.slice(0, dotIdx)
      const ciphertext = payload.slice(dotIdx + 1)

      // Der gepackte Schlüssel steckt in der AAD. Wer daran dreht, zerstört den Tag.
      const tampered = `${prefix}${wrappedKeys.slice(0, -10)}AAAAAAAAAA.${ciphertext}`

      await expect(decryptE2eeHybrid(tampered, bob.privateKeyJwk)).rejects.toThrow()
    }, 30_000)
  })

  describe('Umschlagprüfung', () => {
    it('nimmt die drei Formate an, die es noch gibt', async () => {
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')
      const keys = await generateLocalE2eeKeyPair()

      const hybrid = await encryptE2eeHybrid('Test', keys.publicKeyJwk)
      expect(validateEnvelopeIntegrity(hybrid).valid).toBe(true)
      expect(validateEnvelopeIntegrity(hybrid).prefix).toBe('sv-e2ee-hybrid-v1:')

      const gruppe = `sv-e2ee-group-v1:00112233445566ff.${plausiblerChiffretext()}`
      expect(validateEnvelopeIntegrity(gruppe).valid).toBe(true)

      const rumpf = b64(new TextEncoder().encode('sv-dr-msg-v1:irgendwas'))
      const dr = `sv-e2ee-dr-v1:7.aaaaaaaaaaaa.bbbbbbbbbbbb.${rumpf}`
      expect(validateEnvelopeIntegrity(dr).valid).toBe(true)
    }, 30_000)

    it('weist die abgeschafften Präfixe ab — auch beim Lesen', async () => {
      // Die Gegenprobe zur Aufräumrunde. `sv-e2ee-v1:` und `sv-e2ee-team-v1:`
      // trugen Schlüssel, die sich aus Benutzer- und Gruppenkennung ableiten
      // ließen; `sv-e2ee-ratchet-v1:` war eine SHA-256-Kette ohne
      // Diffie-Hellman-Schritt. Ein Lesepfad, der sie noch annimmt, ist ein
      // Weg zurück.
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')
      const ct = plausiblerChiffretext()

      for (const alt of ['sv-e2ee-v1:', 'sv-e2ee-team-v1:', 'sv-e2ee-ratchet-v1:']) {
        const ergebnis = validateEnvelopeIntegrity(`${alt}${ct}`)
        expect(ergebnis.valid).toBe(false)
        expect(ergebnis.error).toContain('unknown envelope version prefix')
      }
    })

    it('erkennt Klartext im Umschlag', async () => {
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')

      const leak = validateEnvelopeIntegrity(
        'sv-e2ee-hybrid-v1:{"text": "Geheimer Plaintext", "sender_id": 1}'
      )
      expect(leak.valid).toBe(false)
      expect(leak.error).toContain('plaintext payload detected')
    })

    it('verwirft einen Null-Nonce im Gruppenumschlag', async () => {
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')
      const nullIv = b64(new Uint8Array(32))
      const ergebnis = validateEnvelopeIntegrity(`sv-e2ee-group-v1:00112233445566ff.${nullIv}`)
      expect(ergebnis.valid).toBe(false)
      expect(ergebnis.error).toContain('Weak/zero nonce/IV')
    })

    it('besteht auf dem Aufbau der Gruppen- und Ratchet-Umschläge', async () => {
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')
      const ct = plausiblerChiffretext()

      // Gruppe ohne Schlüsselkennung — so sah der alte Umschlag aus.
      expect(validateEnvelopeIntegrity(`sv-e2ee-group-v1:${ct}`).valid).toBe(false)
      // Schlüsselkennung, die keine ist.
      expect(validateEnvelopeIntegrity(`sv-e2ee-group-v1:NICHTHEX.${ct}`).valid).toBe(false)

      const rumpf = b64(new TextEncoder().encode('sv-dr-msg-v1:x'))
      // Zu wenige Teile.
      expect(validateEnvelopeIntegrity(`sv-e2ee-dr-v1:aaaaaaaaaaaa.bbbbbbbbbbbb.${rumpf}`).valid).toBe(false)
      // Absenderkonto ist keine Zahl.
      expect(validateEnvelopeIntegrity(`sv-e2ee-dr-v1:x.aaaaaaaaaaaa.bbbbbbbbbbbb.${rumpf}`).valid).toBe(false)
      // Gerätekennung zu kurz.
      expect(validateEnvelopeIntegrity(`sv-e2ee-dr-v1:7.kurz.bbbbbbbbbbbb.${rumpf}`).valid).toBe(false)
      // Rumpf ohne DIS-Nachrichtenkopf.
      const fremd = b64(new TextEncoder().encode('sv-irgendwas-v1:x'))
      expect(validateEnvelopeIntegrity(`sv-e2ee-dr-v1:7.aaaaaaaaaaaa.bbbbbbbbbbbb.${fremd}`).valid).toBe(false)
    })

    it('weist Leeres, Unbekanntes und Abgeschnittenes ab', async () => {
      const { validateEnvelopeIntegrity } = await import('./e2eeCrypto')
      expect(validateEnvelopeIntegrity('unencrypted:test').valid).toBe(false)
      expect(validateEnvelopeIntegrity('').valid).toBe(false)
      expect(validateEnvelopeIntegrity('sv-e2ee-group-v1:00112233445566ff.short').valid).toBe(false)
    })
  })
})
