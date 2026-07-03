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
import { encryptString, decryptString } from '@msdis/shield/aead';
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

if (!SECRET_KEY) {
  console.error('FATAL: MSM_SECRET_KEY not set');
  process.exit(1);
}
if (!SALT_B64) {
  console.error('FATAL: MSM_DIS_SALT not set');
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

// ── HTTP server ──────────────────────────────────────────────────────────
/** @param {import('node:http').IncomingMessage} req */
function checkAuth(req) {
  if (!TOKEN) return true;
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

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[DIS Sidecar] Listening on http://127.0.0.1:${PORT}`);
});
