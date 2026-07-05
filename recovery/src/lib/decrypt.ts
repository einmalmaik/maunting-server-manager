/**
 * DIS decryption logic for MSM backup `.enc` files.
 *
 * Uses @msdis/shield (Defensive Integration Shield) for all cryptography:
 * - Argon2id key derivation (password + salt -> 256-bit AES key)
 * - AES-256-GCM frame-by-frame decryption
 *
 * The `.enc` frame format (matches the MSM DIS Sidecar `encrypt-stream`):
 * ```
 * [Frame 1]
 *   [4 bytes: frame_length (big-endian uint32)]   // 12 + len(ciphertext||tag)
 *   [12 bytes: nonce]
 *   [frame_length - 12 bytes: ciphertext + 16-byte auth tag]
 * [Frame 2]
 *   ...
 * ```
 *
 * Each frame is independently encrypted with its own random 12-byte nonce and
 * authenticated by a 16-byte AES-GCM tag. The stream is self-delimiting: the
 * decryptor reads 4 bytes of length, then `frame_length` bytes, until EOF.
 *
 * Plaintext chunks are concatenated in order to reproduce the original tar.gz.
 */

import { argon2idRaw, importAesGcmKey } from '@msdis/shield/kdf';
import { aesGcmDecrypt } from '@msdis/shield/aead';

/** Argon2id parameters MUST match the MSM DIS Sidecar exactly. */
export const KDF_PARAMS = {
  memorySize: 131072, // 128 MiB
  iterations: 3,
  parallelism: 4,
  hashLength: 32, // 256-bit AES key
} as const;

/** Frame format constants. */
const FRAME_LEN_FIELD = 4; // big-endian uint32
const NONCE_LEN = 12; // AES-GCM 96-bit nonce
const TAG_LEN = 16; // AES-GCM auth tag (appended to ciphertext)

/** Base64 -> Uint8Array decoder (browser-safe, no Buffer). */
function base64ToBytes(base64: string): Uint8Array {
  const bin = atob(base64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}

/**
 * Derives a 256-bit AES-GCM CryptoKey from a backup password and a raw salt.
 *
 * Uses @msdis/shield `argon2idRaw` with the MSM parameters, then imports the
 * 32-byte raw output as a non-extractable AES-GCM key. The raw key buffer is
 * wiped (`.fill(0)`) immediately after import so it never lingers in memory.
 */
export async function deriveKey(
  password: string,
  salt: Uint8Array,
): Promise<CryptoKey> {
  const rawKey = await argon2idRaw({
    password,
    salt,
    ...KDF_PARAMS,
  });
  const key = await importAesGcmKey(rawKey);
  rawKey.fill(0); // wipe raw key material
  return key;
}

/**
 * Frame-by-frame decryptor. Yields plaintext chunks in order.
 *
 * Throws a clear `Invalid frame format` error when:
 * - the 4-byte length field cannot be read (truncated input),
 * - `frameLength < 12` (cannot contain a nonce),
 * - the frame body extends past the end of the buffer (truncated frame),
 * - the ciphertext region is shorter than the 16-byte auth tag.
 *
 * Throws `Decryption failed` (via @msdis/shield `DisDecryptionError`) when
 * AES-GCM authentication fails (wrong key, tampered ciphertext, AAD mismatch).
 */
export async function* decryptFrames(
  data: Uint8Array,
  key: CryptoKey,
): AsyncGenerator<Uint8Array> {
  let offset = 0;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);

  while (offset < data.length) {
    if (offset + FRAME_LEN_FIELD > data.length) {
      throw new Error('Invalid frame format: truncated frame length');
    }
    const frameLength = view.getUint32(offset);
    offset += FRAME_LEN_FIELD;

    if (frameLength < NONCE_LEN) {
      throw new Error('Invalid frame format: frame length too small for nonce');
    }
    if (offset + frameLength > data.length) {
      throw new Error('Invalid frame format: frame extends past end of data');
    }

    const nonce = data.subarray(offset, offset + NONCE_LEN);
    const ciphertext = data.subarray(offset + NONCE_LEN, offset + frameLength);
    offset += frameLength;

    if (ciphertext.length < TAG_LEN) {
      throw new Error('Invalid frame format: ciphertext shorter than auth tag');
    }

    yield await aesGcmDecrypt(key, nonce, ciphertext);
  }
}

/** Error thrown for a structurally invalid (e.g. empty) `.enc` input. */
export class DecryptError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DecryptError';
  }
}

/**
 * Decrypts a complete MSM backup `.enc` file to the original tar.gz bytes.
 *
 * @param encryptedFile  raw bytes of the `.enc` file
 * @param password       backup password (utf-8 string)
 * @param saltBase64     base64-encoded salt (stored in the MSM database
 *                       panel_settings table under key `backup.salt`;
 *                       the salt itself is not sensitive)
 * @returns              the decrypted tar.gz bytes
 *
 * @throws {@link DecryptError} for empty/structurally invalid input.
 * @throws `DisDecryptionError` (from @msdis/shield) on wrong password / tamper.
 */
export async function decryptBackup(
  encryptedFile: Uint8Array,
  password: string,
  saltBase64: string,
): Promise<Uint8Array> {
  if (encryptedFile.length === 0) {
    throw new DecryptError('Die Datei ist leer oder ungültig.');
  }

  const salt = base64ToBytes(saltBase64);
  const key = await deriveKey(password, salt);

  const chunks: Uint8Array[] = [];
  let totalLength = 0;
  for await (const chunk of decryptFrames(encryptedFile, key)) {
    chunks.push(chunk);
    totalLength += chunk.length;
  }

  // Single-frame fast path: avoid copying when only one chunk was produced.
  if (chunks.length === 1) {
    return chunks[0];
  }

  const result = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}
