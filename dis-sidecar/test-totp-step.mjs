/**
 * Tests for the step in `/totp/verify`.
 *
 * Runs with `node --test test-totp-step.mjs`.
 *
 * Until 5.0.1 the answer was only valid or not, and the panel accepted the same
 * code again for as long as it was valid. The panel now records the step of
 * the last accepted code, so the sidecar has to say which step a code hit.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { generateTotpCode } from '@msdis/shield/totp';

const PORT = 19197;
const TOKEN = 'test-totp-step-token';
const PERIODE_MS = 30_000;

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

async function geheimnis() {
  return (await call('/totp/generate-secret', {})).body.secret;
}

/** Waits if the current step ends within two seconds, so the test does not straddle a boundary. */
async function mitteDesSchritts() {
  const rest = PERIODE_MS - (Date.now() % PERIODE_MS);
  if (rest < 2000) await new Promise((r) => setTimeout(r, rest + 100));
}

test('a current code names the current step', async () => {
  const secret = await geheimnis();
  await mitteDesSchritts();
  const code = generateTotpCode(secret);
  const { body } = await call('/totp/verify', { secret, code });
  assert.deepEqual(body, { valid: true, step: Math.floor(Date.now() / PERIODE_MS) });
});

test('the code of the previous step is still valid and names that step', async () => {
  const secret = await geheimnis();
  await mitteDesSchritts();
  const code = generateTotpCode(secret, { timestamp: Date.now() - PERIODE_MS });
  const { body } = await call('/totp/verify', { secret, code });
  assert.deepEqual(body, { valid: true, step: Math.floor(Date.now() / PERIODE_MS) - 1 });
});

test('a code two steps old is no longer valid', async () => {
  const secret = await geheimnis();
  await mitteDesSchritts();
  const code = generateTotpCode(secret, { timestamp: Date.now() - 2 * PERIODE_MS });
  const { body } = await call('/totp/verify', { secret, code });
  assert.deepEqual(body, { valid: false, step: null });
});

test('wrong code, garbage and a broken secret give no step', async () => {
  const secret = await geheimnis();
  await mitteDesSchritts();
  const richtig = generateTotpCode(secret);
  const falsch = String((Number(richtig) + 1) % 1_000_000).padStart(6, '0');
  for (const [s, code] of [[secret, falsch], [secret, 'abcdef'], [secret, ''], [secret, '1234567'], ['!!kein base32!!', richtig]]) {
    const { body } = await call('/totp/verify', { secret: s, code });
    assert.deepEqual(body, { valid: false, step: null }, `${s} / ${code}`);
  }
});

test('spaces inside the code are ignored', async () => {
  const secret = await geheimnis();
  await mitteDesSchritts();
  const code = generateTotpCode(secret);
  const { body } = await call('/totp/verify', { secret, code: `${code.slice(0, 3)} ${code.slice(3)}` });
  assert.equal(body.valid, true);
});
