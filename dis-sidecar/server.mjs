/**
 * DIS Sidecar for Maunting Server Manager.
 *
 * Tiny local HTTP service that wraps @msdis/shield so the Python backend
 * can use DIS (WebCrypto-based) without importing JS directly.
 *
 * Security:
 * - Listens on 127.0.0.1 ONLY (no external access).
 * - Bearer-token auth prevents other local processes from calling.
 * - Encryption key derived at startup, held in memory, never exported.
 * - No plaintext logged. Only health/errors logged.
 *
 * Start: node --env-file=../backend/.env server.mjs
 */
import http from 'node:http';
import crypto from 'node:crypto';
import {
  aesGcmDecrypt,
  aesGcmEncrypt,
  encryptString,
  decryptString,
} from '@msdis/shield/aead';
import {
  importAesGcmKey,
  deriveHkdfSha256Bits,
  argon2idRaw,
} from '@msdis/shield/kdf';
import { randomBytes } from '@msdis/shield/random';
import { constantTimeEqual } from '@msdis/shield/integrity';
import {
  generateTotpSecret,
  verifyTotpCode,
  buildTotpUri,
} from '@msdis/shield/totp';

// ── Config from env ──────────────────────────────────────────────────────
const PORT = parseInt(process.env.MSM_DIS_SIDECAR_PORT || '9100', 10);
const TOKEN = process.env.MSM_DIS_SIDECAR_TOKEN || '';
const SECRET_KEY = process.env.MSM_SECRET_KEY || '';
const SALT_B64 = process.env.MSM_DIS_SALT || '';
const NODE_ENV = process.env.NODE_ENV || 'production';

if (!SECRET_KEY) {
  console.error('FATAL: MSM_SECRET_KEY not set');
  process.exit(1);
}
if (!SALT_B64) {
  console.error('FATAL: MSM_DIS_SALT not set');
  process.exit(1);
}
if (NODE_ENV === 'production' && (!TOKEN || TOKEN.trim() === '')) {
  console.error('FATAL: MSM_DIS_SIDECAR_TOKEN not set in production');
  process.exit(1);
}

// ── Key derivation at startup ────────────────────────────────────────────
// HKDF-SHA-256 is the correct choice here because SECRET_KEY is already
// high-entropy (random 48-byte URL-safe base64). Argon2id would be
// unnecessary computation for a high-entropy input.
const encoder = new TextEncoder();
const saltBytes = new Uint8Array(Buffer.from(SALT_B64, 'base64'));
const secretKeyBytes = encoder.encode(SECRET_KEY);

const rawKey = await deriveHkdfSha256Bits(secretKeyBytes, {
  info: encoder.encode('MSM-DIS-encryption-v1'),
  salt: saltBytes,
  lengthBits: 256,
});
const encKey = await importAesGcmKey(rawKey);
rawKey.fill(0);
secretKeyBytes.fill(0);

console.log(`[DIS Sidecar] Encryption key derived (HKDF-SHA-256, 256-bit)`);

// ── Password hashing params (DIS KDF v2) ─────────────────────────────────
const PW_SALT_LEN = 16;
const PW_PARAMS = {
  memorySize: 131072, // 128 MiB (DIS KDF v2)
  iterations: 3,
  parallelism: 4,
  hashLength: 32,
};

// ── Backup streaming encryption ──────────────────────────────────────────
// In-memory store of backup encryption keys (key_id -> CryptoKey).
// Keys are non-extractable CryptoKeys held only in memory; lost on restart.
const backupKeys = new Map();

// Argon2id params for backup key derivation (same memory-hard profile as
// password hashing, producing a 256-bit AES-GCM key).
const BACKUP_KDF_PARAMS = {
  memorySize: 131072,
  iterations: 3,
  parallelism: 4,
  hashLength: 32,
};

const STREAM_CHUNK = 64 * 1024; // 64 KiB plaintext per frame
const NONCE_LEN = 12; // AES-GCM 96-bit nonce
const TAG_LEN = 16; // AES-GCM auth tag
const FRAME_LEN_FIELD = 4; // big-endian uint32

// ── HTTP server ──────────────────────────────────────────────────────────
/** @param {import('node:http').IncomingMessage} req */
function checkAuth(req) {
  if (!TOKEN) {
    if (NODE_ENV === 'production') {
      return false;
    }
    return true;
  }
  return req.headers.authorization === `Bearer ${TOKEN}`;
}

/** @param {import('node:http').ServerResponse} res @param {number} code @param {any} data */
function jsonReply(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

/** @param {import('node:http').IncomingMessage} req @returns {Promise<any>} */
async function readJson(req) {
  let body = '';
  for await (const chunk of req) body += chunk;
  return JSON.parse(body);
}

const server = http.createServer(async (req, res) => {
  if (!checkAuth(req)) {
    return jsonReply(res, 401, { error: 'unauthorized' });
  }

  if (req.method === 'GET' && req.url === '/health') {
    return jsonReply(res, 200, { ok: true });
  }

  if (req.method !== 'POST') {
    return jsonReply(res, 405, { error: 'method not allowed' });
  }

  // ── Backup streaming encryption endpoints ──────────────────────────────
  // Handled before the generic JSON body read so the streaming endpoints can
  // consume the raw request body in chunks. Auth has already been checked
  // above (checkAuth runs first on every request), so unauthenticated callers
  // never reach body reading — preventing DoS via large unauthenticated
  // uploads.
  if (req.url === '/backup/encrypt-stream') {
    return handleEncryptStream(req, res);
  }
  if (req.url === '/backup/decrypt-stream') {
    return handleDecryptStream(req, res);
  }
  if (req.url === '/backup/init-key') {
    return handleInitKey(req, res);
  }
  if (req.url === '/backup/invalidate-key') {
    return handleInvalidateKey(req, res);
  }

  let data;
  try {
    data = await readJson(req);
  } catch {
    return jsonReply(res, 400, { error: 'invalid json' });
  }

  try {
    let result;
    switch (req.url) {
      case '/encrypt':
        result = {
          ciphertext: await encryptString(data.plaintext, encKey, data.aad),
        };
        break;

      case '/decrypt':
        result = {
          plaintext: await decryptString(data.ciphertext, encKey, data.aad),
        };
        break;

      case '/hash-password': {
        const salt = randomBytes(PW_SALT_LEN);
        const hash = await argon2idRaw({
          password: data.password,
          salt,
          ...PW_PARAMS,
        });
        result = {
          hash: `msm-pw-v1:${Buffer.from(salt).toString('base64')}:${Buffer.from(hash).toString('base64')}:v2`,
        };
        hash.fill(0);
        break;
      }

      case '/verify-password': {
        const parts = String(data.hash).split(':');
        if (parts.length !== 4 || parts[0] !== 'msm-pw-v1') {
          // Legacy passlib hash — sidecar can't verify, signal to caller
          result = { valid: false, legacy: true };
          break;
        }
        const salt = new Uint8Array(Buffer.from(parts[1], 'base64'));
        const storedHash = new Uint8Array(Buffer.from(parts[2], 'base64'));
        const computed = await argon2idRaw({
          password: data.password,
          salt,
          ...PW_PARAMS,
        });
        const valid = constantTimeEqual(computed, storedHash);
        computed.fill(0);
        storedHash.fill(0);
        result = { valid };
        break;
      }

      case '/totp/generate-secret':
        result = { secret: generateTotpSecret() };
        break;

      case '/totp/verify':
        result = { valid: verifyTotpCode(data.secret, data.code) };
        break;

      case '/totp/build-uri':
        result = {
          uri: buildTotpUri({
            issuer: data.issuer,
            label: data.label,
            secret: data.secret,
          }),
        };
        break;

      default:
        return jsonReply(res, 404, { error: 'not found' });
    }
    return jsonReply(res, 200, result);
  } catch (e) {
    // Do NOT leak plaintext or crypto details. Generic error only.
    const msg = e instanceof Error ? e.name : 'error';
    return jsonReply(res, 400, { error: msg });
  }
});

// ── Backup endpoint handlers ─────────────────────────────────────────────

/** Look up a backup key by id, or reply 400 if missing. Returns CryptoKey or null. */
function lookupBackupKey(res, keyId) {
  if (!keyId || keyId.trim() === '') {
    jsonReply(res, 400, { error: 'MissingKeyId' });
    return null;
  }
  const key = backupKeys.get(keyId);
  if (!key) {
    jsonReply(res, 400, { error: 'KeyNotFound' });
    return null;
  }
  return key;
}

/** POST /backup/init-key — derive Argon2id key from password+salt, store in memory. */
async function handleInitKey(req, res) {
  let body;
  try {
    body = await readJson(req);
  } catch {
    return jsonReply(res, 400, { error: 'invalid json' });
  }
  const password = body?.password;
  const salt = body?.salt;
  if (typeof password !== 'string' || password.length === 0) {
    return jsonReply(res, 400, { error: 'MissingPassword' });
  }
  if (typeof salt !== 'string' || salt.length === 0) {
    return jsonReply(res, 400, { error: 'MissingSalt' });
  }
  let saltBytes;
  try {
    saltBytes = new Uint8Array(Buffer.from(salt, 'base64'));
  } catch {
    return jsonReply(res, 400, { error: 'InvalidSalt' });
  }
  if (saltBytes.length === 0) {
    return jsonReply(res, 400, { error: 'InvalidSalt' });
  }
  let rawKey;
  try {
    rawKey = await argon2idRaw({
      password,
      salt: saltBytes,
      ...BACKUP_KDF_PARAMS,
    });
  } catch {
    return jsonReply(res, 400, { error: 'KeyDerivationFailed' });
  }
  let key;
  try {
    key = await importAesGcmKey(rawKey);
  } finally {
    rawKey.fill(0);
  }
  const keyId = crypto.randomUUID();
  backupKeys.set(keyId, key);
  return jsonReply(res, 200, { key_id: keyId });
}

/** POST /backup/invalidate-key — remove a backup key from memory. */
async function handleInvalidateKey(req, res) {
  let body;
  try {
    body = await readJson(req);
  } catch {
    return jsonReply(res, 400, { error: 'invalid json' });
  }
  const keyId = body?.key_id;
  if (typeof keyId !== 'string' || keyId.length === 0) {
    return jsonReply(res, 400, { error: 'MissingKeyId' });
  }
  // Idempotent: deleting a nonexistent key is safe and returns ok.
  backupKeys.delete(keyId);
  return jsonReply(res, 200, { ok: true });
}

/** POST /backup/encrypt-stream — stream plaintext frames to encrypted frames. */
async function handleEncryptStream(req, res) {
  const keyId = req.headers['x-backup-key-id'];
  const key = lookupBackupKey(res, keyId);
  if (!key) return;

  res.writeHead(200, {
    'Content-Type': 'application/octet-stream',
    'Transfer-Encoding': 'chunked',
  });

  let buffer = Buffer.alloc(0);

  /**
   * Encrypt one plaintext chunk into a frame and write it to the response.
   * Frame: [4-byte BE length][12-byte nonce][ciphertext + 16-byte tag]
   */
  async function flushChunk(chunk) {
    const nonce = randomBytes(NONCE_LEN);
    const ct = await aesGcmEncrypt(key, nonce, new Uint8Array(chunk));
    const len = NONCE_LEN + ct.length; // 12 + ciphertext + tag
    const frame = Buffer.allocUnsafe(FRAME_LEN_FIELD + len);
    frame.writeUInt32BE(len, 0);
    frame.set(nonce, FRAME_LEN_FIELD);
    frame.set(ct, FRAME_LEN_FIELD + NONCE_LEN);
    res.write(frame);
  }

  try {
    for await (const chunk of req) {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= STREAM_CHUNK) {
        const piece = buffer.subarray(0, STREAM_CHUNK);
        buffer = buffer.subarray(STREAM_CHUNK);
        await flushChunk(piece);
      }
    }
    if (buffer.length > 0) {
      await flushChunk(buffer);
    }
    res.end();
  } catch (e) {
    // Encryption with a valid key should not fail; if it does, abort the
    // response without leaking any details. Headers already sent, so we can
    // only destroy the socket.
    res.destroy();
  }
}

/** POST /backup/decrypt-stream — stream encrypted frames back to plaintext. */
async function handleDecryptStream(req, res) {
  const keyId = req.headers['x-backup-key-id'];
  const key = lookupBackupKey(res, keyId);
  if (!key) return;

  // Read the full encrypted input first so that a tampered frame anywhere in
  // the stream results in HTTP 400 with no partial plaintext leaked. The
  // response is still sent with chunked transfer encoding.
  const chunks = [];
  let totalLen = 0;
  try {
    for await (const chunk of req) {
      chunks.push(chunk);
      totalLen += chunk.length;
    }
  } catch {
    return jsonReply(res, 400, { error: 'InvalidStream' });
  }
  const input = Buffer.concat(chunks, totalLen);

  // Parse + decrypt frames into plaintext chunks.
  const outChunks = [];
  let off = 0;
  try {
    while (off < input.length) {
      if (off + FRAME_LEN_FIELD > input.length) {
        throw new Error('TruncatedFrameLength');
      }
      const frameLen = input.readUInt32BE(off);
      off += FRAME_LEN_FIELD;
      if (frameLen < NONCE_LEN) {
        throw new Error('InvalidFrameLength');
      }
      if (off + frameLen > input.length) {
        throw new Error('TruncatedFrame');
      }
      const nonce = input.subarray(off, off + NONCE_LEN);
      const ct = input.subarray(off + NONCE_LEN, off + frameLen);
      off += frameLen;
      if (ct.length < TAG_LEN) {
        throw new Error('InvalidCiphertext');
      }
      const plaintext = await aesGcmDecrypt(key, nonce, ct);
      outChunks.push(Buffer.from(plaintext));
    }
  } catch {
    // Auth-tag mismatch, truncation, or any frame error -> 400, no plaintext.
    return jsonReply(res, 400, { error: 'DecryptionFailed' });
  }

  // All frames decrypted successfully; stream plaintext out (chunked).
  res.writeHead(200, {
    'Content-Type': 'application/octet-stream',
    'Transfer-Encoding': 'chunked',
  });
  for (const c of outChunks) res.write(c);
  res.end();
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[DIS Sidecar] Listening on http://127.0.0.1:${PORT}`);
});
