/**
 * Tests for DIS Sidecar backup streaming encryption endpoints.
 *
 * Runs `node --test test-backup-endpoints.mjs`.
 *
 * Spawns the sidecar on a test port with a test token, then exercises:
 *  - POST /backup/init-key (valid + invalid inputs)
 *  - POST /backup/encrypt-stream + /backup/decrypt-stream (round-trip)
 *  - Frame format (self-delimiting, unique nonces)
 *  - Tamper detection (DecryptionFailed, no partial plaintext)
 *  - Key invalidation + multi-key coexistence
 *  - Bearer auth required on all 4 endpoints
 *  - Empty input round-trip
 *  - /health during streaming
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import crypto from 'node:crypto';
import { request } from 'node:http';

const PORT = 19199;
const TOKEN = 'test-backup-token';
const SALT_B64 = Buffer.from(crypto.randomBytes(16)).toString('base64');

/** Spawn sidecar with test env. Returns { proc, stop }. */
async function startSidecar() {
  const env = {
    ...process.env,
    MSM_DIS_SIDECAR_PORT: String(PORT),
    MSM_DIS_SIDECAR_TOKEN: TOKEN,
    MSM_SECRET_KEY: crypto.randomBytes(32).toString('base64url'),
    MSM_DIS_SALT: SALT_B64,
    NODE_ENV: 'test',
  };
  const proc = spawn(process.execPath, ['server.mjs'], {
    cwd: import.meta.dirname,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderrBuf = '';
  let stdoutBuf = '';
  proc.stdout.on('data', (d) => (stdoutBuf += d.toString()));
  proc.stderr.on('data', (d) => (stderrBuf += d.toString()));
  proc.on('exit', (code, sig) => {
    if (code !== 0 && code !== null) {
      console.error('sidecar exited', code, sig, 'STDOUT:', stdoutBuf, 'STDERR:', stderrBuf);
    }
  });
  // Wait until /health responds
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 100));
    if (proc.exitCode !== null) {
      throw new Error(`sidecar exited early (code=${proc.exitCode}) STDOUT: ${stdoutBuf} STDERR: ${stderrBuf}`);
    }
    try {
      const ok = await health();
      if (ok) return proc;
    } catch {
      /* retry */
    }
  }
  throw new Error(`sidecar did not start. STDOUT: ${stdoutBuf} STDERR: ${stderrBuf}`);
}

function health() {
  return new Promise((resolve, reject) => {
    const req = request(
      `http://127.0.0.1:${PORT}/health`,
      {
        method: 'GET',
        headers: { Authorization: `Bearer ${TOKEN}` },
      },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () =>
          res.statusCode === 200 ? resolve(true) : reject(new Error(`health ${res.statusCode}`)),
        );
      },
    );
    req.on('error', reject);
    req.end();
  });
}

/** Generic JSON POST helper. */
function postJson(path, body, { token = TOKEN } = {}) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = request(
      `http://127.0.0.1:${PORT}${path}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': payload.length,
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const buf = Buffer.concat(chunks);
          let json = null;
          try {
            json = JSON.parse(buf.toString('utf8'));
          } catch {
            /* not json */
          }
          resolve({ status: res.statusCode, body: buf, json, headers: res.headers });
        });
      },
    );
    req.on('error', reject);
    req.end(payload);
  });
}

/** Stream raw bytes to a path, return { status, body, headers, json }. */
function postStream(path, inputBuffer, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = request(
      `http://127.0.0.1:${PORT}${path}`,
      {
        method: 'POST',
        headers: {
          'Content-Length': inputBuffer.length,
          ...headers,
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const buf = Buffer.concat(chunks);
          let json = null;
          try {
            json = JSON.parse(buf.toString('utf8'));
          } catch {
            /* not json */
          }
          resolve({ status: res.statusCode, body: buf, headers: res.headers, json });
        });
      },
    );
    req.on('error', reject);
    req.end(inputBuffer);
  });
}

/** Parse encrypted frames into array of { nonce, ciphertext }. */
function parseFrames(buf) {
  const frames = [];
  let off = 0;
  while (off < buf.length) {
    assert.ok(off + 4 <= buf.length, 'truncated frame length');
    const len = buf.readUInt32BE(off);
    off += 4;
    assert.ok(len >= 12, 'frame length must include 12-byte nonce');
    assert.ok(off + len <= buf.length, 'truncated frame body');
    const nonce = buf.subarray(off, off + 12);
    const ct = buf.subarray(off + 12, off + len);
    frames.push({ nonce: Buffer.from(nonce), ciphertext: Buffer.from(ct) });
    off += len;
  }
  return frames;
}

let proc;
let keyA, keyB;

test.before(async () => {
  proc = await startSidecar();
});
test.after(async () => {
  if (proc) {
    proc.kill('SIGTERM');
    await once(proc, 'exit').catch(() => {});
  }
});

// ── Auth (VAL-DIS-017) ───────────────────────────────────────────────────
test('init-key requires Bearer auth', async () => {
  const r = await postJson('/backup/init-key', { password: 'pw', salt: 'AA==' }, { token: '' });
  assert.equal(r.status, 401);
});

test('encrypt-stream requires Bearer auth (immediate 401, no body read)', async () => {
  // Large body, wrong token — must still get 401 without processing
  const big = Buffer.alloc(1024 * 1024, 0xab);
  const r = await postStream('/backup/encrypt-stream', big, {
    Authorization: 'Bearer wrong-token',
    'X-Backup-Key-Id': 'nope',
  });
  assert.equal(r.status, 401);
});

test('decrypt-stream requires Bearer auth', async () => {
  const r = await postStream('/backup/decrypt-stream', Buffer.from([0, 0, 0, 1, 1]), {
    Authorization: 'Bearer wrong-token',
    'X-Backup-Key-Id': 'nope',
  });
  assert.equal(r.status, 401);
});

test('invalidate-key requires Bearer auth', async () => {
  const r = await postJson('/backup/invalidate-key', { key_id: 'x' }, { token: '' });
  assert.equal(r.status, 401);
});

// ── init-key (VAL-DIS-001, 002, 003) ─────────────────────────────────────
test('init-key returns UUID v4 key_id', async () => {
  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const r = await postJson('/backup/init-key', { password: 'correct horse battery', salt });
  assert.equal(r.status, 200);
  assert.match(r.json.key_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  keyA = r.json.key_id;
});

test('init-key uses Argon2id (measurable delay >= 100ms)', async () => {
  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const start = Date.now();
  const r = await postJson('/backup/init-key', { password: 'pw', salt });
  const elapsed = Date.now() - start;
  assert.equal(r.status, 200);
  assert.ok(elapsed >= 100, `expected Argon2id delay, got ${elapsed}ms`);
});

test('init-key missing password returns 400', async () => {
  const r = await postJson('/backup/init-key', { salt: 'AA==' });
  assert.equal(r.status, 400);
  assert.ok(!r.json.key_id);
});

test('init-key missing salt returns 400', async () => {
  const r = await postJson('/backup/init-key', { password: 'pw' });
  assert.equal(r.status, 400);
  assert.ok(!r.json.key_id);
});

test('init-key empty password returns 400', async () => {
  const r = await postJson('/backup/init-key', { password: '', salt: 'AA==' });
  assert.equal(r.status, 400);
});

test('init-key empty salt returns 400', async () => {
  const r = await postJson('/backup/init-key', { password: 'pw', salt: '' });
  assert.equal(r.status, 400);
});

test('init-key non-base64 salt returns 400', async () => {
  const r = await postJson('/backup/init-key', { password: 'pw', salt: '!!!notbase64!!!' });
  assert.equal(r.status, 400);
});

// ── Round-trip (VAL-DIS-004, 008, 020) ───────────────────────────────────
test('round-trip: 1MB random binary', async () => {
  const plain = crypto.randomBytes(1024 * 1024);
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(enc.status, 200);
  const dec = await postStream('/backup/decrypt-stream', enc.body, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 200);
  assert.equal(plain.toString('hex'), dec.body.toString('hex'));
});

test('round-trip: includes null bytes', async () => {
  const plain = Buffer.from([0, 0, 0, 255, 0, 1, 2, 0, 0]);
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const dec = await postStream('/backup/decrypt-stream', enc.body, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 200);
  assert.deepEqual(Array.from(dec.body), Array.from(plain));
});

test('round-trip: empty input', async () => {
  const enc = await postStream('/backup/encrypt-stream', Buffer.alloc(0), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(enc.status, 200);
  // empty input -> no frames -> empty body
  assert.equal(enc.body.length, 0);
  const dec = await postStream('/backup/decrypt-stream', enc.body, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 200);
  assert.equal(dec.body.length, 0);
});

// ── Frame format (VAL-DIS-006, 007) ──────────────────────────────────────
test('frame format is self-delimiting and nonces unique', async () => {
  // 256KB -> multiple 64KB frames
  const plain = crypto.randomBytes(256 * 1024);
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const frames = parseFrames(enc.body);
  assert.ok(frames.length > 1, 'expected multiple frames for 256KB');
  // Nonces unique
  const nonces = new Set(frames.map((f) => f.nonce.toString('hex')));
  assert.equal(nonces.size, frames.length, 'nonces must be unique per frame');
  // Each ciphertext length == chunk size (last may be smaller); tag is 16 bytes
  for (const f of frames) {
    assert.ok(f.ciphertext.length > 16, 'ciphertext must include 16-byte tag');
  }
  // Parser consumed entire body
  // (parseFrames asserts no leftover by virtue of while loop exiting at off === len)
});

// ── Tamper detection (VAL-DIS-009) ───────────────────────────────────────
test('tamper ciphertext -> 400 DecryptionFailed, no plaintext leaked', async () => {
  const plain = crypto.randomBytes(100);
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const tampered = Buffer.from(enc.body);
  // Flip a ciphertext byte (after 4-byte len + 12-byte nonce)
  tampered[tampered.length - 1] ^= 0x01;
  const dec = await postStream('/backup/decrypt-stream', tampered, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
  assert.equal(dec.json.error, 'DecryptionFailed');
  // No plaintext bytes in response
  assert.ok(!dec.body.includes(plain), 'no plaintext leaked in error response');
});

test('tamper nonce -> 400 DecryptionFailed', async () => {
  const plain = Buffer.from('hello world tamper nonce');
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const tampered = Buffer.from(enc.body);
  tampered[5] ^= 0x01; // nonce byte
  const dec = await postStream('/backup/decrypt-stream', tampered, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
  assert.equal(dec.json.error, 'DecryptionFailed');
});

// ── Wrong key (VAL-DIS-010) ──────────────────────────────────────────────
test('wrong key_id fails decryption', async () => {
  const plain = Buffer.from('secret for key A');
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const keyBResp = await postJson('/backup/init-key', {
    password: 'different password',
    salt: Buffer.from(crypto.randomBytes(16)).toString('base64'),
  });
  keyB = keyBResp.json.key_id;
  const dec = await postStream('/backup/decrypt-stream', enc.body, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyB,
  });
  assert.equal(dec.status, 400);
  assert.equal(dec.json.error, 'DecryptionFailed');
});

// ── Invalid frame format (VAL-DIS-011) ───────────────────────────────────
test('truncated frame length returns 400', async () => {
  const dec = await postStream('/backup/decrypt-stream', Buffer.from([0, 0]), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
});

test('frame_length < 12 returns 400', async () => {
  const buf = Buffer.alloc(4);
  buf.writeUInt32BE(5, 0); // length 5 < 12
  const dec = await postStream('/backup/decrypt-stream', buf, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
});

test('frame_length 0 returns 400', async () => {
  const buf = Buffer.alloc(4); // all zeros -> length 0
  const dec = await postStream('/backup/decrypt-stream', buf, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
});

test('truncated stream mid-frame returns 400', async () => {
  // length says 100 bytes follow, but we only send 10
  const buf = Buffer.alloc(4 + 10);
  buf.writeUInt32BE(100, 0);
  const dec = await postStream('/backup/decrypt-stream', buf, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
});

// ── Missing/invalid key_id (VAL-DIS-012) ─────────────────────────────────
test('encrypt-stream missing X-Backup-Key-Id returns 400', async () => {
  const r = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
  });
  assert.equal(r.status, 400);
});

test('encrypt-stream empty X-Backup-Key-Id returns 400', async () => {
  const r = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': '',
  });
  assert.equal(r.status, 400);
});

test('encrypt-stream nonexistent key_id returns 400', async () => {
  const r = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': '00000000-0000-4000-8000-000000000000',
  });
  assert.equal(r.status, 400);
});

test('decrypt-stream missing X-Backup-Key-Id returns 400', async () => {
  const r = await postStream('/backup/decrypt-stream', Buffer.from([0, 0, 0, 12, ...Buffer.alloc(12)]), {
    Authorization: `Bearer ${TOKEN}`,
  });
  assert.equal(r.status, 400);
});

// ── Key invalidation + coexistence (VAL-DIS-013, 014, 015) ───────────────
test('invalidate-key returns ok and removes key', async () => {
  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const init = await postJson('/backup/init-key', { password: 'temp', salt });
  const kid = init.json.key_id;
  const inv = await postJson('/backup/invalidate-key', { key_id: kid });
  assert.equal(inv.status, 200);
  assert.equal(inv.json.ok, true);
  // Now encrypt with invalidated key fails
  const enc = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': kid,
  });
  assert.equal(enc.status, 400);
});

test('multiple keys coexist; invalidating one does not affect others', async () => {
  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const a = await postJson('/backup/init-key', { password: 'a', salt });
  const b = await postJson('/backup/init-key', { password: 'b', salt });
  // Both work
  const encA = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': a.json.key_id,
  });
  assert.equal(encA.status, 200);
  const encB = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': b.json.key_id,
  });
  assert.equal(encB.status, 200);
  // Invalidate A
  await postJson('/backup/invalidate-key', { key_id: a.json.key_id });
  // B still works
  const encB2 = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': b.json.key_id,
  });
  assert.equal(encB2.status, 200);
  // A fails
  const encA2 = await postStream('/backup/encrypt-stream', Buffer.from('x'), {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': a.json.key_id,
  });
  assert.equal(encA2.status, 400);
  // cleanup
  await postJson('/backup/invalidate-key', { key_id: b.json.key_id });
});

test('invalidate nonexistent key_id is safe (no 500), health ok', async () => {
  const r = await postJson('/backup/invalidate-key', { key_id: '00000000-0000-4000-8000-000000000000' });
  assert.ok(r.status === 200 || r.status === 400, `got ${r.status}`);
  assert.notEqual(r.status, 500);
  assert.ok(await health());
});

// ── Deterministic derivation (VAL-DIS-019) ───────────────────────────────
test('same password+salt produces equivalent keys (cross-decrypt)', async () => {
  const password = 'restore-test-password';
  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const a = await postJson('/backup/init-key', { password, salt });
  const b = await postJson('/backup/init-key', { password, salt });
  assert.notEqual(a.json.key_id, b.json.key_id, 'key_ids differ');
  const plain = Buffer.from('cross-decrypt content');
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': a.json.key_id,
  });
  const dec = await postStream('/backup/decrypt-stream', enc.body, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': b.json.key_id,
  });
  assert.equal(dec.status, 200);
  assert.equal(dec.body.toString('utf8'), 'cross-decrypt content');
  await postJson('/backup/invalidate-key', { key_id: a.json.key_id });
  await postJson('/backup/invalidate-key', { key_id: b.json.key_id });
});

// ── No plaintext in logs/error responses (VAL-DIS-018) ───────────────────
test('error responses contain only {error: string}, no plaintext/secrets', async () => {
  const secret = 'TOPSECRET-marker-12345';
  const plain = Buffer.from(secret);
  const enc = await postStream('/backup/encrypt-stream', plain, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  const tampered = Buffer.from(enc.body);
  tampered[tampered.length - 1] ^= 0xff;
  const dec = await postStream('/backup/decrypt-stream', tampered, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  assert.equal(dec.status, 400);
  const bodyStr = dec.body.toString('utf8');
  assert.ok(!bodyStr.includes(secret), 'no plaintext in error response');
  assert.ok(bodyStr.startsWith('{') && bodyStr.endsWith('}'), 'error response is JSON only');
});

// ── /health during streaming (VAL-DIS-021) ───────────────────────────────
test('/health returns 200 while a large encrypt is in-flight', async () => {
  // Issue a large encrypt but check health concurrently. We use a moderately
  // large input so the encrypt is still processing when we hit /health.
  const big = crypto.randomBytes(8 * 1024 * 1024);
  const encPromise = postStream('/backup/encrypt-stream', big, {
    Authorization: `Bearer ${TOKEN}`,
    'X-Backup-Key-Id': keyA,
  });
  // Health should respond while encrypt runs
  const ok = await health();
  assert.ok(ok);
  const enc = await encPromise;
  assert.equal(enc.status, 200);
});
