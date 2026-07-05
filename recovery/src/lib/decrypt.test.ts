/**
 * Vitest unit tests for the DIS decryption logic (`src/lib/decrypt.ts`).
 *
 * Covers the validation assertions VAL-DECRYPT-001..007 plus the
 * cross-cutting type/test gates. All crypto runs through @msdis/shield so the
 * tests exercise the same WebCrypto + hash-wasm code path used in the Tauri
 * WebView at runtime.
 *
 * These tests run in the `node` environment (see vite.config.ts) because DIS
 * relies on the global `crypto.subtle` provider, which Node 22 exposes
 * natively. Argon2id is memory-hard (128 MiB / 3 iterations), so a 60s
 * per-test timeout is configured.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { decryptBackup, decryptFrames, deriveKey, KDF_PARAMS, DecryptError } from './decrypt';
import {
  createTestEnc,
  deriveTestKey,
  gzipBytes,
  utf8,
  bytesToBase64,
  sha256,
  STREAM_CHUNK,
} from './test-fixture';
import { aesGcmDecrypt } from '@msdis/shield/aead';
import { randomBytes } from '@msdis/shield/random';

// Known fixture credentials (the salt is not sensitive).
const PASSWORD = 'correct-backup-password-123';
const WRONG_PASSWORD = 'this-is-not-the-password';
const SALT = new Uint8Array([
  0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10,
]);
const SALT_BASE64 = bytesToBase64(SALT);

// Shared derived key (one Argon2id cost) reused for fixture encryption.
let testKey: CryptoKey;

beforeAll(async () => {
  testKey = await deriveTestKey(PASSWORD, SALT);
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-001: Argon2id key derivation
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-001: deriveKey (Argon2id)', () => {
  it('KDF_PARAMS match the MSM DIS Sidecar spec exactly', () => {
    expect(KDF_PARAMS).toEqual({
      memorySize: 131072,
      iterations: 3,
      parallelism: 4,
      hashLength: 32,
    });
  });

  it('returns a non-extractable AES-GCM 256-bit CryptoKey usable for decryption', async () => {
    const key = await deriveKey(PASSWORD, SALT);
    const alg = key.algorithm as KeyAlgorithm & { length?: number };
    expect(key.type).toBe('secret');
    expect(key.extractable).toBe(false);
    expect(alg.name).toBe('AES-GCM');
    expect(alg.length).toBe(256);
    expect(key.usages).toContain('decrypt');

    // The derived key must actually decrypt a known-good frame.
    const nonce = randomBytes(12);
    const plaintext = utf8('round-trip-key-check');
    const { aesGcmEncrypt } = await import('@msdis/shield/aead');
    const ciphertext = await aesGcmEncrypt(key, nonce, plaintext);
    const decrypted = await aesGcmDecrypt(key, nonce, ciphertext);
    expect(Array.from(decrypted)).toEqual(Array.from(plaintext));
  });

  it('produces the same key as the test-fixture derivation (same params)', async () => {
    const a = await deriveKey(PASSWORD, SALT);
    // Encrypt with one derived key, decrypt with another derived independently.
    const nonce = randomBytes(12);
    const plaintext = utf8('deterministic-kdf');
    const { aesGcmEncrypt } = await import('@msdis/shield/aead');
    const ciphertext = await aesGcmEncrypt(a, nonce, plaintext);
    const b = await deriveTestKey(PASSWORD, SALT);
    const decrypted = await aesGcmDecrypt(b, nonce, ciphertext);
    expect(Array.from(decrypted)).toEqual(Array.from(plaintext));
  });
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-002 + VAL-DECRYPT-007: round-trip + gzip magic
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-002: frame-by-frame round-trip', () => {
  // A gzip payload larger than one 64 KiB frame so the stream spans >= 2 frames.
  const payload = new Uint8Array(STREAM_CHUNK + 2048);
  for (let i = 0; i < payload.length; i++) {
    payload[i] = i & 0xff;
  }
  const originalTarGz = gzipBytes(payload);
  let enc: Uint8Array;

  beforeAll(async () => {
    enc = await createTestEnc(originalTarGz, testKey);
  });

  it('spans at least two frames', () => {
    // First 4 bytes are the first frame length; the stream must be larger than
    // a single frame to confirm multi-frame handling.
    const firstFrameLen = new DataView(enc.buffer).getUint32(0);
    expect(enc.length).toBeGreaterThan(FRAME_LEN_FIELD + firstFrameLen);
  });

  it('decrypts to the original tar.gz (byte-for-byte)', async () => {
    const decrypted = await decryptBackup(enc, PASSWORD, SALT_BASE64);
    expect(decrypted.length).toBe(originalTarGz.length);
    expect(await sha256(decrypted)).toBe(await sha256(originalTarGz));
  });

  it('preserves chunk order (first bytes match original)', async () => {
    const decrypted = await decryptBackup(enc, PASSWORD, SALT_BASE64);
    expect(decrypted[0]).toBe(originalTarGz[0]);
    expect(decrypted[1]).toBe(originalTarGz[1]);
    expect(decrypted[STREAM_CHUNK]).toBe(originalTarGz[STREAM_CHUNK]);
  });
});

describe('VAL-DECRYPT-007: decrypted output is valid gzip (magic 1f 8b)', () => {
  it('output starts with 0x1f 0x8b', async () => {
    const original = gzipBytes(utf8('hello-gzip'));
    const enc = await createTestEnc(original, testKey);
    const decrypted = await decryptBackup(enc, PASSWORD, SALT_BASE64);
    expect(decrypted[0]).toBe(0x1f);
    expect(decrypted[1]).toBe(0x8b);
  });
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-003: wrong password
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-003: wrong password causes a clear error', () => {
  it('rejects (never resolves with empty/garbage) on wrong password', async () => {
    const enc = await createTestEnc(gzipBytes(utf8('secret')), testKey);
    await expect(decryptBackup(enc, WRONG_PASSWORD, SALT_BASE64)).rejects.toThrow();
  });

  it('does not produce a valid gzip stream on wrong password', async () => {
    const enc = await createTestEnc(gzipBytes(utf8('secret')), testKey);
    let produced: Uint8Array | null = null;
    try {
      produced = await decryptBackup(enc, WRONG_PASSWORD, SALT_BASE64);
    } catch {
      // expected
    }
    expect(produced).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-004: corrupt / truncated .enc file
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-004: corrupt .enc file causes a clear error', () => {
  it('rejects when a ciphertext bit is flipped (auth-tag failure)', async () => {
    const enc = await createTestEnc(gzipBytes(utf8('tamper-me')), testKey);
    const corrupt = enc.slice();
    // Flip a byte inside the first frame's ciphertext region (after 4 + 12).
    corrupt[16] = corrupt[16] ^ 0x01;
    await expect(decryptBackup(corrupt, PASSWORD, SALT_BASE64)).rejects.toThrow();
  });

  it('throws "Invalid frame format" when the stream is truncated mid-frame', async () => {
    const enc = await createTestEnc(gzipBytes(new Uint8Array(100_000)), testKey);
    const truncated = enc.subarray(0, enc.length - 10);
    await expect(decryptBackup(truncated, PASSWORD, SALT_BASE64)).rejects.toThrow(
      /Invalid frame format/,
    );
  });

  it('throws when frame_length is too small to hold a nonce', async () => {
    const bad = new Uint8Array(8);
    new DataView(bad.buffer).setUint32(0, 5); // frame_length = 5 < 12
    await expect(
      (async () => {
        for await (const _ of decryptFrames(bad, testKey)) {
          // drain
        }
      })(),
    ).rejects.toThrow(/frame length too small for nonce/);
  });

  it('throws when the length field itself is truncated', async () => {
    const bad = new Uint8Array(2); // only 2 bytes, cannot read uint32
    await expect(
      (async () => {
        for await (const _ of decryptFrames(bad, testKey)) {
          // drain
        }
      })(),
    ).rejects.toThrow(/truncated frame length/);
  });
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-005: empty .enc file
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-005: empty .enc file causes a clear error', () => {
  it('throws a DecryptError for empty input', async () => {
    await expect(decryptBackup(new Uint8Array(0), PASSWORD, SALT_BASE64)).rejects.toBeInstanceOf(
      DecryptError,
    );
  });

  it('does not hang (completes within timeout)', async () => {
    const start = Date.now();
    await expect(
      decryptBackup(new Uint8Array(0), PASSWORD, SALT_BASE64),
    ).rejects.toThrow();
    expect(Date.now() - start).toBeLessThan(5_000);
  });
});

// ---------------------------------------------------------------------------
// VAL-DECRYPT-006: large .enc file (10 MB+)
// ---------------------------------------------------------------------------

describe('VAL-DECRYPT-006: large .enc file (10 MB+) decrypts successfully', () => {
  // 10 MiB + a little headroom, deterministic pattern to avoid entropy cost.
  const SIZE = 10 * 1024 * 1024 + 1234;
  const originalTarGz = gzipBytes(new Uint8Array(SIZE).fill(0xab));
  let enc: Uint8Array;

  beforeAll(async () => {
    enc = await createTestEnc(originalTarGz, testKey);
  }, 120_000);

  it('encrypted stream is at least 10 MB', () => {
    expect(enc.length).toBeGreaterThanOrEqual(10 * 1024 * 1024);
  });

  it('spans many frames', () => {
    const firstFrameLen = new DataView(enc.buffer).getUint32(0);
    expect(firstFrameLen).toBeGreaterThan(0);
    // 10 MB / 64 KiB ~ 160 frames.
    expect(enc.length / (FRAME_LEN_FIELD + 12 + 32)).toBeGreaterThan(100);
  });

  it('decrypts to the exact original bytes (byte-for-byte)', async () => {
    const decrypted = await decryptBackup(enc, PASSWORD, SALT_BASE64);
    expect(decrypted.length).toBe(originalTarGz.length);
    expect(await sha256(decrypted)).toBe(await sha256(originalTarGz));
  }, 120_000);

  it('decrypted large output has gzip magic bytes', async () => {
    const decrypted = await decryptBackup(enc, PASSWORD, SALT_BASE64);
    expect(decrypted[0]).toBe(0x1f);
    expect(decrypted[1]).toBe(0x8b);
  }, 120_000);
});

// Frame length field size, kept in sync with decrypt.ts.
const FRAME_LEN_FIELD = 4;
