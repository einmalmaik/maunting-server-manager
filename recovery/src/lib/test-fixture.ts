/**
 * Test-fixture helpers for the DIS decryption tests.
 *
 * Creates `.enc` byte streams with the exact frame format produced by the MSM
 * DIS Sidecar `encrypt-stream` endpoint, using @msdis/shield `aesGcmEncrypt`.
 * This lets the round-trip tests verify that `decryptBackup` correctly
 * reverses real MSM-encrypted backups.
 *
 * Frame format (matches `dis-sidecar/server.mjs`):
 * ```
 * [4-byte BE uint32 frame_length][12-byte nonce][ciphertext + 16-byte tag]
 *   frame_length = NONCE_LEN + ciphertext.length
 * ```
 *
 * NOTE: Test-only module. Never shipped to production builds.
 */

import { argon2idRaw, importAesGcmKey } from '@msdis/shield/kdf';
import { aesGcmEncrypt } from '@msdis/shield/aead';
import { randomBytes } from '@msdis/shield/random';
import { KDF_PARAMS } from './decrypt';

const FRAME_LEN_FIELD = 4;
const NONCE_LEN = 12;
/** Plaintext chunk size per frame (64 KiB), matches MSM DIS Sidecar. */
export const STREAM_CHUNK = 64 * 1024;

/** UTF-8 -> Uint8Array (browser-safe, no Node Buffer in tests). */
export function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

/** Uint8Array -> base64 (browser-safe). */
export function bytesToBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) {
    bin += String.fromCharCode(bytes[i]);
  }
  return btoa(bin);
}

/** Base64 -> Uint8Array. */
export function base64ToBytes(base64: string): Uint8Array {
  const bin = atob(base64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}

/** Imports a non-extractable AES-GCM CryptoKey derived from password+salt. */
export async function deriveTestKey(
  password: string,
  salt: Uint8Array,
): Promise<CryptoKey> {
  const rawKey = await argon2idRaw({ password, salt, ...KDF_PARAMS });
  const key = await importAesGcmKey(rawKey);
  rawKey.fill(0);
  return key;
}

/**
 * Encrypts `plaintext` into an MSM-format `.enc` byte stream.
 *
 * Splits the plaintext into 64 KiB chunks (the final chunk may be smaller),
 * encrypts each with a fresh random 12-byte nonce via `aesGcmEncrypt`, and
 * prefixes each frame with a 4-byte big-endian `frame_length`.
 */
export async function createTestEnc(
  plaintext: Uint8Array,
  key: CryptoKey,
): Promise<Uint8Array> {
  const frames: Uint8Array[] = [];

  for (let i = 0; i < plaintext.length; i += STREAM_CHUNK) {
    const chunk = plaintext.subarray(i, Math.min(i + STREAM_CHUNK, plaintext.length));
    const nonce = randomBytes(NONCE_LEN);
    const ciphertext = await aesGcmEncrypt(key, nonce, chunk);

    const frame = new Uint8Array(FRAME_LEN_FIELD + NONCE_LEN + ciphertext.length);
    new DataView(frame.buffer).setUint32(0, NONCE_LEN + ciphertext.length);
    frame.set(nonce, FRAME_LEN_FIELD);
    frame.set(ciphertext, FRAME_LEN_FIELD + NONCE_LEN);
    frames.push(frame);
  }

  // Empty plaintext -> empty stream (matches DIS Sidecar: no frames written).
  if (frames.length === 0) {
    return new Uint8Array(0);
  }

  const total = frames.reduce((sum, f) => sum + f.length, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const f of frames) {
    result.set(f, offset);
    offset += f.length;
  }
  return result;
}

/**
 * Builds a minimal valid gzip stream (`1f 8b` magic) containing `payload`.
 *
 * The recovery app only needs to verify gzip magic bytes and round-trip
 * integrity, so we synthesise a gzip member with a single DEFLATE block.
 * This avoids a Node `zlib` dependency and keeps the fixture deterministic.
 *
 * Layout (RFC 1952):
 *   header (10 bytes) | DEFLATE stream | CRC32 (4 bytes) | ISIZE (4 bytes)
 *
 * The DEFLATE stream uses a single stored (BTYPE=00) block for payloads up to
 * 65535 bytes, and a series of stored blocks for larger payloads.
 */
export function gzipBytes(payload: Uint8Array): Uint8Array {
  // Header (10 bytes): ID1 ID2 CM FLG MTIME(4) XFL OS
  const header = [
    0x1f, 0x8b, // magic
    0x08, // CM = deflate
    0x00, // FLG = 0
    0x00, 0x00, 0x00, 0x00, // MTIME = 0
    0x00, // XFL
    0xff, // OS = unknown
  ];

  // DEFLATE body: stored blocks (BTYPE=00). Each block: 1 byte header + 4 bytes
  // LEN/NLEN + LEN bytes. Max LEN per block is 65535.
  const deflated: number[] = [];
  let remaining = payload.length;
  let pos = 0;
  if (remaining === 0) {
    // A single empty final stored block.
    deflated.push(0x01); // BFINAL=1, BTYPE=00
    deflated.push(0x00, 0x00); // LEN = 0
    deflated.push(0xff, 0xff); // NLEN = 0xffff
  } else {
    while (remaining > 0) {
      const blockLen = Math.min(remaining, 0xffff);
      const isFinal = remaining - blockLen === 0;
      deflated.push(isFinal ? 0x01 : 0x00); // BFINAL flag, BTYPE=00
      deflated.push(blockLen & 0xff, (blockLen >> 8) & 0xff); // LEN (LE)
      deflated.push(~blockLen & 0xff, (~blockLen >> 8) & 0xff); // NLEN (LE)
      for (let i = 0; i < blockLen; i++) {
        deflated.push(payload[pos + i]);
      }
      pos += blockLen;
      remaining -= blockLen;
    }
  }

  const crc = crc32(payload);
  const crcBytes = [crc & 0xff, (crc >> 8) & 0xff, (crc >> 16) & 0xff, (crc >>> 24) & 0xff];
  const isize = payload.length & 0xffffffff;
  const isizeBytes = [
    isize & 0xff,
    (isize >> 8) & 0xff,
    (isize >> 16) & 0xff,
    (isize >>> 24) & 0xff,
  ];

  return new Uint8Array([...header, ...deflated, ...crcBytes, ...isizeBytes]);
}

/** Standard CRC-32 (IEEE 802.3 polynomial 0xEDB88320). */
function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** SHA-256 of two equal-length byte arrays, compared in constant time. */
export async function sha256(a: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', a as BufferSource);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}
