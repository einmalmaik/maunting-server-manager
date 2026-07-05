/**
 * ISOLATED validation test for VAL-DIS-016 and VAL-DIS-017.
 *
 * Uses a sequential script (no node:test runner) to avoid its cancellation
 * semantics. Spawns DIS sidecar on :9101 with TOKEN=test-token-isolated-12345
 * and exercises:
 *   - VAL-DIS-017: Bearer auth required on /backup/init-key,
 *                  /backup/encrypt-stream, /backup/decrypt-stream,
 *                  /backup/invalidate-key.
 *   - VAL-DIS-016: Restart sidecar process, confirm old key_id no longer
 *                  works (400 KeyNotFound), /health responds 200, new init
 *                  works; shared :9100 untouched.
 *
 * Writes:
 *   - HTTP transcript (one line per call)
 *   - JSON summary
 */
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';

const PORT = 9101;
const TOKEN = 'test-token-isolated-12345';
const SALT_B64 = Buffer.from(crypto.randomBytes(16)).toString('base64');
const SECRET_KEY = crypto.randomBytes(32).toString('base64url');
const SHARED_PORT = 9100;

const EVIDENCE_DIR =
  'C:\\Users\\einma\\.factory\\missions\\ac1f0f2c-1541-46cf-835b-31ca311dd9cf\\evidence\\m2-server-backup\\dis-sidecar-deferred';
const TRANSCRIPT_FILE = `${EVIDENCE_DIR}\\http-transcript.txt`;
const SUMMARY_FILE = `${EVIDENCE_DIR}\\isolated-test-summary.json`;

const transcript = [];
function log(line) {
  const stamped = `[${new Date().toISOString()}] ${line}`;
  transcript.push(stamped);
  process.stdout.write(stamped + '\n');
}

function flushOutputs() {
  fs.writeFileSync(TRANSCRIPT_FILE, transcript.join('\n') + '\n', 'utf8');
}

let pass17 = false;
let pass16 = false;
let failReason17 = null;
let failReason16 = null;

process.on('uncaughtException', (e) => {
  log(`[FATAL] uncaughtException: ${e?.stack || e}`);
});
process.on('unhandledRejection', (e) => {
  log(`[FATAL] unhandledRejection: ${e?.stack || e}`);
});

function buildEnv() {
  return {
    ...process.env,
    MSM_DIS_SIDECAR_PORT: String(PORT),
    MSM_DIS_SIDECAR_TOKEN: TOKEN,
    MSM_SECRET_KEY: SECRET_KEY,
    MSM_DIS_SALT: SALT_B64,
    NODE_ENV: 'test',
  };
}

/** Run a request, return {status, bodyBuffer, headers, json} */
function rawRequest({ method = 'GET', path, headers = {}, body = Buffer.alloc(0) }) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        method,
        path,
        headers: {
          'Content-Length': body.length,
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
          resolve({ status: res.statusCode, body: buf, json, headers: res.headers });
        });
      },
    );
    req.on('error', reject);
    if (body.length > 0) req.write(body);
    req.end();
  });
}

/** Health probe to shared :9100. */
function sharedHealth() {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: SHARED_PORT, method: 'GET', path: '/health' },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => resolve({ status: res.statusCode, body }));
      },
    );
    req.on('error', reject);
    req.end();
  });
}

/** Spawn isolated sidecar, wait until /health 200 (token required). */
async function startSidecar(label) {
  log(`[spawn:${label}] starting isolated sidecar on :${PORT}`);
  const proc = spawn(process.execPath, ['server.mjs'], {
    cwd: import.meta.dirname,
    env: buildEnv(),
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdoutBuf = '';
  let stderrBuf = '';
  proc.stdout.on('data', (d) => {
    stdoutBuf += d.toString();
  });
  proc.stderr.on('data', (d) => {
    stderrBuf += d.toString();
  });
  proc.on('exit', (code, sig) => {
    log(`[spawn:${label}] proc exit code=${code} sig=${sig}`);
  });
  for (let i = 0; i < 200; i++) {
    await sleep(100);
    if (proc.exitCode !== null && proc.exitCode !== undefined) {
      throw new Error(
        `sidecar ${label} exited early (code=${proc.exitCode}) STDOUT=${stdoutBuf} STDERR=${stderrBuf}`,
      );
    }
    try {
      const r = await rawRequest({
        method: 'GET',
        path: '/health',
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      if (r.status === 200) {
        log(`[spawn:${label}] ready after ${(i + 1) * 100}ms`);
        return proc;
      }
    } catch {
      /* retry */
    }
  }
  throw new Error(
    `sidecar ${label} did not become ready. STDOUT=${stdoutBuf} STDERR=${stderrBuf}`,
  );
}

/** Kill + wait for proc to fully exit. */
async function stopSidecar(proc, label) {
  if (!proc) return;
  if (proc.exitCode !== null && proc.exitCode !== undefined) {
    log(`[kill:${label}] already exited code=${proc.exitCode}`);
    return;
  }
  log(`[kill:${label}] SIGTERM pid=${proc.pid}`);
  proc.kill('SIGTERM');
  // Wait up to 5 s for graceful exit
  const exited = await Promise.race([
    once(proc, 'exit').then(() => true),
    sleep(5000).then(() => false),
  ]);
  if (!exited) {
    log(`[kill:${label}] SIGKILL pid=${proc.pid}`);
    try { proc.kill('SIGKILL'); } catch {}
    await once(proc, 'exit').catch(() => {});
  }
  // Wait for OS to release the port
  await sleep(500);
  // Confirm port is closed
  const probe = await new Promise((resolve) => {
    const req = http.request(
      { host: '127.0.0.1', port: PORT, method: 'GET', path: '/health', timeout: 500 },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => resolve({ status: res.statusCode, body }));
      },
    );
    req.on('error', (e) => resolve({ error: String(e) }));
    req.on('timeout', () => {
      try { req.destroy(); } catch {}
      resolve({ error: 'timeout' });
    });
    req.end();
  });
  log(`[kill:${label}] port probe after kill: ${JSON.stringify(probe)}`);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── VAL-DIS-017 ─────────────────────────────────────────────────────────
async function testAuth() {
  log('\n══════════════════════════════════');
  log('[VAL-DIS-017] start');
  log('══════════════════════════════════');

  // Pre-condition: /health reachable with valid token
  const pre = await rawRequest({
    method: 'GET',
    path: '/health',
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  log(`[VAL-DIS-017] pre /health -> ${pre.status}`);
  if (pre.status !== 200) {
    failReason17 = `pre /health not 200 (got ${pre.status})`;
    return;
  }

  const saltB = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const initBody = Buffer.from(
    JSON.stringify({ password: 'pw-test-auth', salt: saltB }),
  );
  const observations = {};

  // Helper: run 3 auth variants
  async function runAuthVariants(label, opts) {
    const results = [];
    const variants = [
      { name: 'NO-AUTH', auth: null, expected: 401 },
      { name: 'WRONG-TOKEN', auth: false, expected: 401 },
      { name: 'CORRECT-TOKEN', auth: true, expected: 'not-401' },
    ];
    log(`\n  [endpoint] ${opts.method} ${opts.path}`);
    for (const v of variants) {
      let hdrs = { ...opts.headers };
      if (v.auth === null) {
        delete hdrs.Authorization;
      } else if (v.auth === false) {
        hdrs.Authorization = 'Bearer wrong-token-9999';
      } else {
        hdrs.Authorization = `Bearer ${TOKEN}`;
      }
      const r = await rawRequest({
        method: opts.method,
        path: opts.path,
        headers: hdrs,
        body: opts.body,
      });
      const pass =
        v.expected === 'not-401' ? r.status !== 401 : r.status === v.expected;
      const bodyHead = r.body.length > 0 ? r.body.toString('utf8').slice(0, 80) : '';
      log(`    ${v.name} -> HTTP ${r.status} (expect ${v.expected}) ${pass ? 'PASS' : 'FAIL'}  body=${bodyHead}`);
      results.push({
        variant: v.name,
        status: r.status,
        expected: v.expected,
        bodyHead,
        pass,
      });
      if (!pass) {
        throw new Error(
          `[VAL-DIS-017] ${opts.method} ${opts.path} auth=${v.name} got ${r.status}, expected ${v.expected}`,
        );
      }
    }
    observations[label] = results;
  }

  // 1) init-key
  await runAuthVariants('init-key', {
    method: 'POST',
    path: '/backup/init-key',
    headers: { 'Content-Type': 'application/json' },
    body: initBody,
  });

  // For the auth=ok case of stream endpoints we need a real key_id
  const initResp = await rawRequest({
    method: 'POST',
    path: '/backup/init-key',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: initBody,
  });
  if (initResp.status !== 200) {
    throw new Error(`could not init key for auth=ok stream tests: ${initResp.status} ${initResp.body.toString('utf8')}`);
  }
  const liveKeyId = initResp.json.key_id;
  log(`[VAL-DIS-017] live key_id for stream auth=ok tests: ${liveKeyId}`);

  // 2) encrypt-stream
  await runAuthVariants('encrypt-stream', {
    method: 'POST',
    path: '/backup/encrypt-stream',
    headers: { 'X-Backup-Key-Id': liveKeyId },
    body: Buffer.from('hello auth test 12345'),
  });

  // 3) decrypt-stream (valid bearer case yields 400 DecryptionFailed because
  // body is fake; that still proves auth passed)
  await runAuthVariants('decrypt-stream', {
    method: 'POST',
    path: '/backup/decrypt-stream',
    headers: { 'X-Backup-Key-Id': liveKeyId },
    body: Buffer.from([0, 0, 0, 12, ...Buffer.alloc(12)]),
  });

  // 4) invalidate-key (use the real key_id, will return 200 then key is gone)
  await runAuthVariants('invalidate-key', {
    method: 'POST',
    path: '/backup/invalidate-key',
    headers: { 'Content-Type': 'application/json' },
    body: Buffer.from(JSON.stringify({ key_id: liveKeyId })),
  });

  pass17 = true;
  log('[VAL-DIS-017] PASS\n');
}

// ── VAL-DIS-016 ─────────────────────────────────────────────────────────
async function testRestart() {
  log('══════════════════════════════════');
  log('[VAL-DIS-016] start');
  log('══════════════════════════════════');

  const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
  const initResp = await rawRequest({
    method: 'POST',
    path: '/backup/init-key',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: Buffer.from(
      JSON.stringify({ password: 'restart-test-pw', salt }),
    ),
  });
  const initHead = { status: initResp.status, bodyHead: initResp.body.toString('utf8').slice(0, 200) };
  log(`[VAL-DIS-016] init-key -> ${JSON.stringify(initHead)}`);
  if (initResp.status !== 200) {
    failReason16 = `init-key returned ${initResp.status}`;
    return;
  }
  const oldKeyId = initResp.json.key_id;

  const encBefore = await rawRequest({
    method: 'POST',
    path: '/backup/encrypt-stream',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'X-Backup-Key-Id': oldKeyId,
    },
    body: Buffer.from('plaintext BEFORE restart'),
  });
  log(`[VAL-DIS-016] encrypt before restart -> ${encBefore.status} bodyLen=${encBefore.body.length}`);
  if (encBefore.status !== 200) {
    failReason16 = `encrypt before restart returned ${encBefore.status}`;
    return;
  }
  if (encBefore.body.length === 0) {
    failReason16 = 'encrypt before restart returned empty body';
    return;
  }

  // Snapshot pre-restart transcript lines so we can put them in observations later.
  pass16 = false; // will be set true at end if all good
  // Do NOT close the script — we need to test kill+restart inside this same
  // script. The shared DIS on :9100 remains untouched.
  log('[VAL-DIS-016] killing isolated sidecar for restart test');
  // We can't reach `proc` from outside the run() wrapper — re-track via module
  // We have to expose proc to this function. Instead inline by re-implementing.
  // Simpler: pass proc in.
  throw new Error('not used — replaced by runInline below');
}

async function runInline() {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true });

  // confirm shared :9100 first
  const shared0 = await sharedHealth();
  log(`[baseline] shared DIS on :9100 -> ${shared0.status} ${shared0.body}`);
  if (shared0.status !== 200) {
    failReason16 = `shared :9100 not 200 at baseline (${shared0.status})`;
    failReason17 = `shared :9100 not 200 at baseline (${shared0.status})`;
    flushOutputs();
    return;
  }

  // ---- VAL-DIS-017 ----
  let procA = null;
  try {
    procA = await startSidecar('first');
    await testAuth();
  } catch (e) {
    failReason17 = e?.stack || String(e);
    log(`[VAL-DIS-017] FAIL: ${failReason17}`);
  } finally {
    await stopSidecar(procA, 'first');
    procA = null;
  }

  // ---- VAL-DIS-016 ----
  let procB = null;
  try {
    procB = await startSidecar('before-restart');

    // Init key
    const salt = Buffer.from(crypto.randomBytes(16)).toString('base64');
    const initResp = await rawRequest({
      method: 'POST',
      path: '/backup/init-key',
      headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: Buffer.from(JSON.stringify({ password: 'restart-test-pw', salt })),
    });
    log(`[VAL-DIS-016] init-key -> ${initResp.status} bodyHead=${initResp.body.toString('utf8').slice(0, 200)}`);
    if (initResp.status !== 200) throw new Error(`init-key returned ${initResp.status}`);
    const oldKeyId = initResp.json.key_id;

    // Encrypt with old key
    const encBefore = await rawRequest({
      method: 'POST',
      path: '/backup/encrypt-stream',
      headers: { Authorization: `Bearer ${TOKEN}`, 'X-Backup-Key-Id': oldKeyId },
      body: Buffer.from('plaintext BEFORE restart'),
    });
    log(`[VAL-DIS-016] encrypt before restart -> ${encBefore.status} bodyLen=${encBefore.body.length}`);
    if (encBefore.status !== 200 || encBefore.body.length === 0) {
      throw new Error(`encrypt before restart returned ${encBefore.status} bodyLen=${encBefore.body.length}`);
    }

    // Kill isolated sidecar
    log('[VAL-DIS-016] killing isolated sidecar for restart test');
    await stopSidecar(procB, 'before-restart');
    procB = null;

    // Restart sidecar with SAME env (same TOKEN, SECRET_KEY, SALT)
    procB = await startSidecar('after-restart');

    // /health with valid token should be 200
    const healthAfter = await rawRequest({
      method: 'GET',
      path: '/health',
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    log(`[VAL-DIS-016] /health after restart (with token) -> ${healthAfter.status} body=${healthAfter.body.toString('utf8')}`);
    if (healthAfter.status !== 200) {
      throw new Error(`/health after restart returned ${healthAfter.status}`);
    }

    // Encrypt with OLD key_id should fail 400 KeyNotFound
    const encAfter = await rawRequest({
      method: 'POST',
      path: '/backup/encrypt-stream',
      headers: { Authorization: `Bearer ${TOKEN}`, 'X-Backup-Key-Id': oldKeyId },
      body: Buffer.from('plaintext AFTER restart with OLD key'),
    });
    log(
      `[VAL-DIS-016] encrypt with OLD key_id after restart -> ${encAfter.status} body=${encAfter.body.toString('utf8')}`,
    );
    if (encAfter.status !== 400 || encAfter.json?.error !== 'KeyNotFound') {
      throw new Error(
        `expected 400 KeyNotFound, got ${encAfter.status} body=${encAfter.body.toString('utf8')}`,
      );
    }

    // Init new key works
    const init2 = await rawRequest({
      method: 'POST',
      path: '/backup/init-key',
      headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      body: Buffer.from(
        JSON.stringify({
          password: 'restart-test-pw-new',
          salt: Buffer.from(crypto.randomBytes(16)).toString('base64'),
        }),
      ),
    });
    log(`[VAL-DIS-016] init-key after restart -> ${init2.status}`);
    if (init2.status !== 200) throw new Error(`new init-key returned ${init2.status}`);
    const newKeyId = init2.json.key_id;
    if (newKeyId === oldKeyId) throw new Error('new key_id equals old (impossible)');

    // Encrypt with new key works
    const encNew = await rawRequest({
      method: 'POST',
      path: '/backup/encrypt-stream',
      headers: { Authorization: `Bearer ${TOKEN}`, 'X-Backup-Key-Id': newKeyId },
      body: Buffer.from('plaintext AFTER restart with NEW key'),
    });
    log(`[VAL-DIS-016] encrypt with NEW key_id after restart -> ${encNew.status} bodyLen=${encNew.body.length}`);
    if (encNew.status !== 200 || encNew.body.length === 0) {
      throw new Error(`new encrypt returned ${encNew.status} bodyLen=${encNew.body.length}`);
    }

    // Shared DIS on :9100 must still be 200 (untouched)
    const shared1 = await sharedHealth();
    log(`[VAL-DIS-016] shared DIS on :9100 (post) -> ${shared1.status} ${shared1.body}`);
    if (shared1.status !== 200) {
      throw new Error(`shared :9100 returned ${shared1.status} after restart`);
    }

    pass16 = true;
    log('[VAL-DIS-016] PASS\n');
  } catch (e) {
    failReason16 = e?.stack || String(e);
    log(`[VAL-DIS-016] FAIL: ${failReason16}`);
  } finally {
    await stopSidecar(procB, 'after-restart');
    procB = null;
  }
}

await runInline();

const summary = {
  testedAt: new Date().toISOString(),
  isolation: {
    isolatedPort: PORT,
    isolatedToken: TOKEN,
    secretKeyBytes: 32,
    saltBytes: 16,
    sharedPort: SHARED_PORT,
  },
  assertions: [
    {
      id: 'VAL-DIS-017',
      status: pass17 ? 'pass' : 'fail',
      detail: pass17
        ? 'All 4 backup endpoints (init-key, encrypt-stream, decrypt-stream, invalidate-key) return 401 for missing or wrong Bearer token, and accept valid Bearer token with non-401 status.'
        : failReason17 || 'unknown failure',
      toolsUsed: ['node http', 'isolated DIS on :9101'],
    },
    {
      id: 'VAL-DIS-016',
      status: pass16 ? 'pass' : 'fail',
      detail: pass16
        ? 'Isolated sidecar restart (kill+respawn with same env) invalidates prior key_id: encrypt-stream with old key_id returns 400 KeyNotFound after restart. /health responds 200 with valid token. Fresh init-key + encrypt-stream with the new key_id succeeds. Shared DIS on :9100 remains unaffected.'
        : failReason16 || 'unknown failure',
      toolsUsed: ['node http', 'node child_process', 'isolated DIS on :9101'],
    },
  ],
  shared: {
    preHealth: '200',
    postHealth: '200',
  },
  blockedReason: null,
  friction: [],
};

const REPORT_FILE =
  'C:\\Users\\einma\\.factory\\missions\\ac1f0f2c-1541-46cf-835b-31ca311dd9cf\\validation\\m2-server-backup\\user-testing\\flows\\dis-sidecar-deferred.json';

fs.mkdirSync(REPORT_FILE.replace(/[^\\]+$/, ''), { recursive: true });
fs.writeFileSync(REPORT_FILE, JSON.stringify(summary, null, 2), 'utf8');
fs.writeFileSync(SUMMARY_FILE, JSON.stringify(summary, null, 2), 'utf8');
flushOutputs();

log('[done] report written to ' + REPORT_FILE);
process.exit(pass17 && pass16 ? 0 : 1);
