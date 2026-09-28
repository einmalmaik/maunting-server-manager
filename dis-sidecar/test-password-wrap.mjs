/**
 * Tests for wrapping pre-DIS passlib hashes into `msm-pw-v1:`.
 *
 * Runs with `node --test test-password-wrap.mjs`.
 *
 * Old accounts still hold `$argon2id$v=19$...` from passlib. Rehashing needs
 * the plaintext, so /wrap-legacy-password hashes the old digest with DIS
 * instead, and /verify-password recomputes that digest from the password.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';

const PORT = 19199;
const TOKEN = 'test-password-wrap-token';

// Erzeugt mit passlib (CryptContext(schemes=['argon2'])), wie das Panel
// vor DIS Passwoerter ablegte.
const PASSWORT = 'Alt-Passwort 1!';
const PASSLIB_HASH = [
  '$argon2id$v=19$m=65536,t=3,p=4',
  'CWEsZWxNqZUy5lxLKSUEAA',
  'y+7fIjmE2RiaDvWKGF13U2DSFdmUl8+16vYwCQ8njUc',
].join('$');

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

test('wrapped passlib hash carries msm-pw-v1 and the old parameters', async () => {
  const res = await call('/wrap-legacy-password', { hash: PASSLIB_HASH });
  assert.equal(res.status, 200);
  const teile = res.body.hash.split(':');
  assert.equal(teile.length, 4);
  assert.equal(teile[0], 'msm-pw-v1');
  assert.equal(teile[3], 'alt.65536.3.4.32.CWEsZWxNqZUy5lxLKSUEAA');
  assert.ok(!res.body.hash.includes('y+7fIjmE2RiaDvWKGF13U2DSFdmUl8'));
});

test('wrapped hash accepts the old password and nothing else', async () => {
  const { body } = await call('/wrap-legacy-password', { hash: PASSLIB_HASH });
  const richtig = await call('/verify-password', { password: PASSWORT, hash: body.hash });
  assert.deepEqual(richtig.body, { valid: true });
  const falsch = await call('/verify-password', { password: 'Alt-Passwort 2!', hash: body.hash });
  assert.deepEqual(falsch.body, { valid: false });
});

test('tampered old parameters never verify', async () => {
  const { body } = await call('/wrap-legacy-password', { hash: PASSLIB_HASH });
  const teile = body.hash.split(':');
  teile[3] = teile[3].replace('alt.65536.3.4', 'alt.65536.2.4');
  const res = await call('/verify-password', { password: PASSWORT, hash: teile.join(':') });
  assert.deepEqual(res.body, { valid: false });
  teile[3] = 'v3';
  const unbekannt = await call('/verify-password', { password: PASSWORT, hash: teile.join(':') });
  assert.deepEqual(unbekannt.body, { valid: false });
});

test('fresh hashes still end in v2 and verify directly', async () => {
  const { body } = await call('/hash-password', { password: PASSWORT });
  assert.ok(body.hash.endsWith(':v2'), body.hash);
  const res = await call('/verify-password', { password: PASSWORT, hash: body.hash });
  assert.deepEqual(res.body, { valid: true });
});

test('anything but a passlib argon2id hash is refused', async () => {
  for (const hash of ['msm-pw-v1:a:b:v2', '$argon2i$v=19$m=65536,t=3,p=4$YWJj$YWJj', '']) {
    const res = await call('/wrap-legacy-password', { hash });
    assert.equal(res.status, 400, hash);
    assert.equal(res.body.error, 'DisLegacyHashError');
  }
});
