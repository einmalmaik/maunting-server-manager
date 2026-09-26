/**
 * Tests for the `msm-dis-v1:` envelope on /encrypt and /decrypt.
 *
 * Runs with `node --test test-encrypt-envelope.mjs`.
 *
 * A stored DIS value must be recognisable as such in any table. Before
 * 26.09.2026 /encrypt returned bare base64, and every existing row still holds
 * that form, so /decrypt has to keep reading it.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';

const PORT = 19198;
const TOKEN = 'test-envelope-token';
const PREFIX = 'msm-dis-v1:';

let proc;

async function call(path, body) {
  const res = await fetch(`http://127.0.0.1:${PORT}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

before(async () => {
  proc = spawn(process.execPath, ['server.mjs'], {
    cwd: import.meta.dirname,
    env: {
      ...process.env,
      MSM_DIS_SIDECAR_PORT: String(PORT),
      MSM_DIS_SIDECAR_TOKEN: TOKEN,
      MSM_SECRET_KEY: crypto.randomBytes(32).toString('base64url'),
      MSM_DIS_SALT: crypto.randomBytes(16).toString('base64'),
      NODE_ENV: 'test',
    },
    stdio: 'ignore',
  });
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 100));
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/health`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      if (res.status === 200) return;
    } catch {
      /* not up yet */
    }
  }
  throw new Error('sidecar did not start');
});

after(() => proc?.kill());

test('encrypt writes the msm-dis-v1 prefix', async () => {
  const res = await call('/encrypt', { plaintext: 'geheim', aad: 'msm:test' });
  assert.equal(res.status, 200);
  assert.ok(res.body.ciphertext.startsWith(PREFIX), res.body.ciphertext);
  assert.ok(!res.body.ciphertext.includes('geheim'));
});

test('prefixed value round-trips with its aad', async () => {
  const enc = await call('/encrypt', { plaintext: 'hallo welt', aad: 'msm:test' });
  const dec = await call('/decrypt', { ciphertext: enc.body.ciphertext, aad: 'msm:test' });
  assert.equal(dec.status, 200);
  assert.equal(dec.body.plaintext, 'hallo welt');
});

test('wrong aad still fails as DisDecryptionError', async () => {
  const enc = await call('/encrypt', { plaintext: 'x', aad: 'msm:a' });
  const dec = await call('/decrypt', { ciphertext: enc.body.ciphertext, aad: 'msm:b' });
  assert.equal(dec.status, 400);
  assert.equal(dec.body.error, 'DisDecryptionError');
});

test('bare base64 from before the envelope stays readable', async () => {
  const enc = await call('/encrypt', { plaintext: 'altbestand', aad: 'msm:test' });
  const bare = enc.body.ciphertext.slice(PREFIX.length);
  const dec = await call('/decrypt', { ciphertext: bare, aad: 'msm:test' });
  assert.equal(dec.status, 200);
  assert.equal(dec.body.plaintext, 'altbestand');
});

test('an unknown envelope version fails loudly, not as a bad value', async () => {
  const enc = await call('/encrypt', { plaintext: 'x' });
  const future = 'msm-dis-v2:' + enc.body.ciphertext.slice(PREFIX.length);
  const dec = await call('/decrypt', { ciphertext: future });
  assert.equal(dec.status, 400);
  // Not DisDecryptionError: callers treat that as "value is useless" and some
  // overwrite it. A newer format must never be overwritten by an older panel.
  assert.equal(dec.body.error, 'DisUnsupportedFormatVersionError');
});

test('an empty envelope is rejected as an unusable value', async () => {
  const dec = await call('/decrypt', { ciphertext: PREFIX });
  assert.equal(dec.status, 400);
  assert.equal(dec.body.error, 'DisInvalidArgumentError');
});

// ── /decrypt-many and /blind-index (memory keys, 26.09.2026) ──────────────

test('decrypt-many returns every value in order, null for the unreadable one', async () => {
  const a = await call('/encrypt', { plaintext: 'eins', aad: 'msm:a' });
  const b = await call('/encrypt', { plaintext: 'zwei', aad: 'msm:b' });
  const res = await call('/decrypt-many', {
    items: [
      { ciphertext: a.body.ciphertext, aad: 'msm:a' },
      { ciphertext: b.body.ciphertext, aad: 'msm:falsch' },
      { ciphertext: b.body.ciphertext.slice(PREFIX.length), aad: 'msm:b' },
    ],
  });
  assert.equal(res.status, 200);
  assert.deepEqual(res.body.plaintexts, ['eins', null, 'zwei']);
});

test('decrypt-many fails loudly on a newer envelope version', async () => {
  const a = await call('/encrypt', { plaintext: 'x' });
  const res = await call('/decrypt-many', {
    items: [{ ciphertext: 'msm-dis-v2:' + a.body.ciphertext.slice(PREFIX.length) }],
  });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'DisUnsupportedFormatVersionError');
});

test('batches above the cap are refused', async () => {
  const res = await call('/blind-index', { values: new Array(10001).fill('x') });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'DisBatchError');
});

test('blind-index is deterministic, keyed and distinct per value', async () => {
  const res = await call('/blind-index', { values: ['user:1\nzeitzone', 'user:1\nzeitzone', 'user:2\nzeitzone'] });
  assert.equal(res.status, 200);
  const [a, b, c] = res.body.indices;
  assert.match(a, /^[0-9a-f]{64}$/);
  assert.equal(a, b);
  assert.notEqual(a, c);
  // Keyed: a plain SHA-256 of the value must not reproduce it.
  const plain = crypto.createHash('sha256').update('user:1\nzeitzone').digest('hex');
  assert.notEqual(a, plain);
});
