/**
 * DIS Sidecar for Maunting Service Manager.
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
import {
  constantTimeEqual,
  importHmacSha256Key,
  hmacSha256WithKey,
  sha256Bytes,
} from '@msdis/shield/integrity';
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

const altchaKeyBytes = await deriveHkdfSha256Bits(secretKeyBytes, {
  info: encoder.encode('MSM-ALTCHA-HMAC-v1'),
  salt: saltBytes,
  lengthBits: 256,
});
const altchaHmacKey = await importHmacSha256Key(altchaKeyBytes, ['sign', 'verify']);
altchaKeyBytes.fill(0);
secretKeyBytes.fill(0);

console.log(`[DIS Sidecar] Encryption key derived (HKDF-SHA-256, 256-bit)`);
console.log(`[DIS Sidecar] ALTCHA HMAC key derived (HKDF-SHA-256, 256-bit)`);

// ── ALTCHA CAPTCHA state ──────────────────────────────────────────────────
// Map of signature -> expiresAtMillis. Prevents replay attacks.
const altchaReplayCache = new Map();
const MAX_ALTCHA_CACHE_SIZE = 50000;

function cleanAltchaReplayCache() {
  const now = Date.now();
  for (const [sig, exp] of altchaReplayCache.entries()) {
    if (exp <= now) {
      altchaReplayCache.delete(sig);
    }
  }
}

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

// A frame carries at most STREAM_CHUNK plaintext plus nonce and tag. Anything
// larger is not a frame we ever wrote.
//
// Audit 2026-09-22: the length field was read as an arbitrary uint32 and the
// reader then waited for that many bytes to arrive. A single frame header of
// 0xFFFFFFFF made the decryptor accumulate up to 4 GiB in one Buffer before it
// could conclude anything — an OOM kill of the one process every crypto
// operation of the panel depends on.
const MAX_FRAME_LEN = NONCE_LEN + STREAM_CHUNK + TAG_LEN + 64;

// ── Backup stream format v2 ──────────────────────────────────────────────
//
// Audit 2026-09-22: v1 frames were each authenticated on their own, but nothing
// bound a frame to its *position*. Every frame used AAD=null, so an attacker
// holding the encrypted backup (a compromised S3 bucket, NAS, or anyone who can
// write where backups land) could reorder frames, duplicate them, or cut the
// stream short — and every single frame still verified. The restore then wrote
// authentic-but-rearranged plaintext: an old database page back over a new one,
// silently, with a valid GCM tag on every chunk.
//
// v2 binds each frame to its index and marks the final frame, so reordering,
// duplication and truncation all fail the tag check.
//
//   stream := MAGIC || frame*
//   frame  := [4B BE len][12B nonce][ct||tag]
//   AAD    := [8B BE index][1B final]
//
// The magic cannot collide with a v1 stream: a v1 stream opens with a frame
// length field, and 'M' (0x4D) as its high byte would mean a frame of ~1.3 GB —
// far beyond MAX_FRAME_LEN. Streams without the magic are read as v1 so that
// backups written before this change stay restorable; v1 is never written.
const STREAM_MAGIC_V2 = Buffer.from('MSMBKP2\n', 'ascii');

/** AAD for frame `index`; `final` marks the last frame of the stream. */
function frameAad(index, final) {
  const aad = new Uint8Array(9);
  const view = new DataView(aad.buffer);
  view.setBigUint64(0, BigInt(index), false);
  aad[8] = final ? 1 : 0;
  return aad;
}

// ── HTTP server ──────────────────────────────────────────────────────────
const EXPECTED_AUTH = Buffer.from(`Bearer ${TOKEN}`, 'utf8');

/**
 * Constant-time bearer check.
 *
 * Audit 2026-09-22: this used `===` on the header string. JavaScript's string
 * comparison exits at the first differing byte, so the response time leaked how
 * long a prefix matched. Over loopback — where the whole threat model is "some
 * other local process must not be able to call us" — an attacker can recover
 * the token byte by byte and then use `/decrypt` as a universal oracle against
 * every secret the panel holds. `constantTimeEqual` was already imported for
 * password verification; it just wasn't used on the door.
 */
function checkAuth(req) {
  if (!TOKEN) {
    if (NODE_ENV === 'production') {
      return false;
    }
    return true;
  }
  const supplied = Buffer.from(String(req.headers.authorization || ''), 'utf8');
  // Length is not a secret (the token length is fixed by config), but the
  // comparison must not short-circuit on content.
  if (supplied.length !== EXPECTED_AUTH.length) {
    // Still burn a comparison so a wrong length costs the same as a wrong byte.
    constantTimeEqual(new Uint8Array(EXPECTED_AUTH), new Uint8Array(EXPECTED_AUTH));
    return false;
  }
  return constantTimeEqual(new Uint8Array(supplied), new Uint8Array(EXPECTED_AUTH));
}

/** @param {import('node:http').ServerResponse} res @param {number} code @param {any} data */
function jsonReply(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

// A JSON request to this service is a secret, a hash or a TOTP code — none of
// them is megabytes long. Without a ceiling a single request could grow the
// heap until the process dies, taking every crypto operation of the panel with
// it (fail-closed means: nothing works any more).
const MAX_JSON_BODY = 8 * 1024 * 1024; // 8 MiB

/** @param {import('node:http').IncomingMessage} req @returns {Promise<any>} */
async function readJson(req) {
  let body = '';
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_JSON_BODY) {
      throw new Error('PayloadTooLarge');
    }
    body += chunk;
  }
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
  // One-shot raw AES key for MSM Agent handoff (Phase 6). Not stored in backupKeys.
  if (req.url === '/backup/derive-raw-key') {
    return handleDeriveRawKey(req, res);
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

      case '/altcha/challenge': {
        cleanAltchaReplayCache();
        const maxnumber = 50000;
        const expiresInSeconds = 300; // 5 minutes
        const expiresAt = Math.floor(Date.now() / 1000) + expiresInSeconds;
        const saltHex = Buffer.from(randomBytes(16)).toString('hex');
        const salt = `${saltHex}?expires=${expiresAt}`;
        const targetNumber = crypto.randomInt(0, maxnumber + 1);

        const challengeBytes = await sha256Bytes(encoder.encode(salt + targetNumber));
        const challenge = Buffer.from(challengeBytes).toString('hex');

        const sigBytes = await hmacSha256WithKey(altchaHmacKey, encoder.encode(challenge));
        const signature = Buffer.from(sigBytes).toString('hex');

        result = {
          algorithm: 'SHA-256',
          challenge,
          maxnumber,
          maxNumber: maxnumber,
          salt,
          signature,
        };
        break;
      }

      case '/altcha/verify': {
        cleanAltchaReplayCache();
        const payload = data?.payload;
        if (typeof payload !== 'string' || !payload.trim()) {
          result = { valid: false };
          break;
        }

        let parsed;
        try {
          const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
          const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
          const jsonStr = Buffer.from(padded, 'base64').toString('utf8');
          parsed = JSON.parse(jsonStr);
        } catch {
          result = { valid: false };
          break;
        }

        if (
          !parsed ||
          parsed.algorithm !== 'SHA-256' ||
          typeof parsed.challenge !== 'string' ||
          typeof parsed.salt !== 'string' ||
          typeof parsed.signature !== 'string' ||
          typeof parsed.number !== 'number' ||
          !Number.isInteger(parsed.number) ||
          parsed.number < 0
        ) {
          result = { valid: false };
          break;
        }

        // Check expiry in salt (reject expired or absurd future timestamps)
        const qIndex = parsed.salt.indexOf('?');
        if (qIndex === -1) {
          result = { valid: false };
          break;
        }
        const params = new URLSearchParams(parsed.salt.slice(qIndex + 1));
        const expiresStr = params.get('expires');
        if (!expiresStr) {
          result = { valid: false };
          break;
        }
        const expiresSec = parseInt(expiresStr, 10);
        const nowSec = Math.floor(Date.now() / 1000);
        if (isNaN(expiresSec) || expiresSec < nowSec || expiresSec > nowSec + 600) {
          result = { valid: false };
          break;
        }

        // Replay check
        if (altchaReplayCache.has(parsed.signature)) {
          result = { valid: false };
          break;
        }

        // Verify HMAC signature in constant time
        const expectedSigBytes = await hmacSha256WithKey(altchaHmacKey, encoder.encode(parsed.challenge));
        const expectedSig = Buffer.from(expectedSigBytes).toString('hex');

        const expectedSigBuf = Buffer.from(expectedSig, 'utf8');
        const suppliedSigBuf = Buffer.from(parsed.signature, 'utf8');
        if (
          expectedSigBuf.length !== suppliedSigBuf.length ||
          !constantTimeEqual(new Uint8Array(expectedSigBuf), new Uint8Array(suppliedSigBuf))
        ) {
          result = { valid: false };
          break;
        }

        // Verify Proof-of-Work
        const computedChallengeBytes = await sha256Bytes(encoder.encode(parsed.salt + parsed.number));
        const computedChallenge = Buffer.from(computedChallengeBytes).toString('hex');
        if (computedChallenge.toLowerCase() !== parsed.challenge.toLowerCase()) {
          result = { valid: false };
          break;
        }

        // Record in replay cache with FIFO eviction guarantee to prevent replay bypass under load
        if (altchaReplayCache.size >= MAX_ALTCHA_CACHE_SIZE) {
          const oldestKey = altchaReplayCache.keys().next().value;
          if (oldestKey !== undefined) {
            altchaReplayCache.delete(oldestKey);
          }
        }
        altchaReplayCache.set(parsed.signature, expiresSec * 1000);

        result = { valid: true };
        break;
      }

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

/**
 * POST /backup/derive-raw-key — Argon2id → base64 AES-256 key for agent handoff.
 * Same KDF params as init-key. Key is NOT stored in backupKeys (RAM only on caller).
 * Response key material must not be logged by the panel.
 */
async function handleDeriveRawKey(req, res) {
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
  try {
    const key_b64 = Buffer.from(rawKey).toString('base64');
    return jsonReply(res, 200, { key_b64 });
  } finally {
    rawKey.fill(0);
  }
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
  res.write(STREAM_MAGIC_V2);

  let buffer = Buffer.alloc(0);
  let frameIndex = 0;

  /**
   * Encrypt one plaintext chunk into a frame and write it to the response.
   * Frame: [4-byte BE length][12-byte nonce][ciphertext + 16-byte tag]
   * AAD binds the frame to its index and to whether it ends the stream.
   */
  async function flushChunk(chunk, final) {
    const nonce = randomBytes(NONCE_LEN);
    const ct = await aesGcmEncrypt(key, nonce, new Uint8Array(chunk), frameAad(frameIndex, final));
    frameIndex += 1;
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
        await flushChunk(piece, false);
      }
    }
    // Always emit a final frame — it is the receipt that the stream was not cut
    // short. For empty input that is a single frame with empty plaintext.
    await flushChunk(buffer, true);
    res.end();
  } catch (e) {
    // Encryption with a valid key should not fail; if it does, abort the
    // response without leaking any details. Headers already sent, so we can
    // only destroy the socket.
    res.destroy();
  }
}

/** POST /backup/decrypt-stream — stream encrypted frames back to plaintext.
 *
 * Frames are decrypted and written to the response incrementally as they
 * arrive, so the entire encrypted object is never buffered in memory. This
 * allows large backups (100MB+) to be decrypted without OOM.
 *
 * Security: each frame carries its own AES-GCM auth tag. A successfully
 * decrypted frame's plaintext is authenticated — writing it immediately is
 * safe. If a later frame is tampered or truncated, the socket is destroyed
 * so the caller receives an interrupted stream rather than corrupt output.
 * If the error occurs before any frame has been written (e.g. first frame
 * tampered, invalid frame length, or truncated input), a clean HTTP 400
 * {error: "DecryptionFailed"} is returned with no plaintext leaked.
 */
async function handleDecryptStream(req, res) {
  const keyId = req.headers['x-backup-key-id'];
  const key = lookupBackupKey(res, keyId);
  if (!key) return;

  let headersSent = false;
  let buffer = Buffer.alloc(0);
  let version = null; // null = not yet determined, 1 = legacy, 2 = position-bound
  let frameIndex = 0;
  let sawFinalFrame = false;

  /** Send 200 + chunked headers on first successful plaintext write. */
  function beginStreaming() {
    if (!headersSent) {
      res.writeHead(200, {
        'Content-Type': 'application/octet-stream',
        'Transfer-Encoding': 'chunked',
      });
      headersSent = true;
    }
  }

  /**
   * Decrypt one frame. For v2 the frame's AAD must match its position; the
   * final frame is recognised by falling back to the `final` AAD exactly once.
   * Returns the plaintext, or null if the frame does not authenticate.
   */
  async function decryptFrame(nonce, ct) {
    if (version === 1) {
      try {
        return await aesGcmDecrypt(key, nonce, ct);
      } catch {
        return null;
      }
    }
    try {
      const plain = await aesGcmDecrypt(key, nonce, ct, frameAad(frameIndex, false));
      frameIndex += 1;
      return plain;
    } catch {
      // Not a body frame at this position — the only other thing it may be is
      // the final frame for this very index. Anything else (reordered,
      // duplicated, foreign) fails both and is rejected below.
    }
    try {
      const plain = await aesGcmDecrypt(key, nonce, ct, frameAad(frameIndex, true));
      frameIndex += 1;
      sawFinalFrame = true;
      return plain;
    } catch {
      return null;
    }
  }

  /** Handle a decryption/frame error: 400 if no plaintext sent yet, else destroy socket. */
  function failDecrypt() {
    if (headersSent) {
      // Plaintext from earlier (authenticated) frames was already written.
      // We cannot retroactively send a 400, so destroy the socket to signal
      // the error. The caller detects the interrupted stream.
      res.destroy();
    } else {
      jsonReply(res, 400, { error: 'DecryptionFailed' });
    }
  }

  try {
    for await (const chunk of req) {
      buffer = buffer.length > 0 ? Buffer.concat([buffer, chunk]) : chunk;

      // Determine the stream version once, from the leading magic.
      if (version === null) {
        if (buffer.length < STREAM_MAGIC_V2.length) continue;
        if (buffer.subarray(0, STREAM_MAGIC_V2.length).equals(STREAM_MAGIC_V2)) {
          version = 2;
          buffer = Buffer.from(buffer.subarray(STREAM_MAGIC_V2.length));
        } else {
          version = 1;
        }
      }

      // Process as many complete frames as are available in the buffer.
      let off = 0;
      while (off + FRAME_LEN_FIELD <= buffer.length) {
        const frameLen = buffer.readUInt32BE(off);
        off += FRAME_LEN_FIELD;
        if (frameLen < NONCE_LEN || frameLen > MAX_FRAME_LEN) {
          // Too small to hold a nonce, or larger than anything we ever wrote.
          return failDecrypt();
        }
        if (off + frameLen > buffer.length) {
          // Not enough data for this frame yet — rewind and wait for more.
          off -= FRAME_LEN_FIELD;
          break;
        }
        const nonce = buffer.subarray(off, off + NONCE_LEN);
        const ct = buffer.subarray(off + NONCE_LEN, off + frameLen);
        off += frameLen;
        if (ct.length < TAG_LEN) {
          return failDecrypt();
        }
        if (sawFinalFrame) {
          // Data after the frame that claimed to end the stream: appended or
          // replayed frames. Refuse rather than hand the caller a longer file
          // than the one that was signed off.
          return failDecrypt();
        }
        const plaintext = await decryptFrame(nonce, ct);
        if (plaintext === null) {
          // Auth-tag mismatch: tamper, wrong key, or a frame out of position.
          return failDecrypt();
        }
        beginStreaming();
        res.write(Buffer.from(plaintext));
      }

      // Retain only the unprocessed tail. Copy into a new small Buffer so the
      // large accumulated buffer can be garbage-collected (subarray keeps the
      // original backing store alive).
      buffer = off > 0 ? Buffer.from(buffer.subarray(off)) : buffer;
    }

    // Request stream ended. Any leftover bytes mean a truncated final frame.
    if (buffer.length > 0) {
      return failDecrypt();
    }

    // A v2 stream that never presented its final frame was cut short — the one
    // manipulation a per-frame tag cannot notice on its own.
    if (version === 2 && !sawFinalFrame) {
      return failDecrypt();
    }

    // Clean end (empty input yields an empty plaintext response with 200).
    beginStreaming();
    res.end();
  } catch {
    failDecrypt();
  }
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[DIS Sidecar] Listening on http://127.0.0.1:${PORT}`);
});
