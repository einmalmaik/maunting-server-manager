/**
 * Test fixture generator for user-testing validation.
 *
 * Creates a real .enc file encrypted with @msdis/shield that can be
 * dragged into the recovery app via the browser File API.
 *
 * Output:
 *   - C:\Users\einma\AppData\Local\Temp\msm-test-backup.enc  (the .enc file)
 *   - Prints password + base64 salt to stdout
 */

import { argon2idRaw, importAesGcmKey } from '@msdis/shield/kdf';
import { aesGcmEncrypt } from '@msdis/shield/aead';
import { randomBytes } from '@msdis/shield/random';
import { writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const KDF_PARAMS = {
  memorySize: 131072,
  iterations: 3,
  parallelism: 4,
  hashLength: 32,
};

const PASSWORD = 'TestBackup2026!';
const SALT_BYTES = randomBytes(16);
const SALT_BASE64 = Buffer.from(SALT_BYTES).toString('base64');

// Create a real tar.gz with known content so the mock extractTarGz can
// potentially parse it. The content includes files that match the
// validation contract test cases.
const files = {
  'readme.txt': 'MSM Backup Recovery Test\nDies ist eine Testdatei mit Umlauten: äöüß\n',
  'manifest.json': JSON.stringify({
    backup_id: 'test-001',
    created_at: '2026-07-05T12:00:00Z',
    server: 'test-server',
    version: '2.0',
    files: ['readme.txt', 'data/config.json', 'data/notes.txt'],
  }, null, 2),
  'data/config.json': JSON.stringify({ host: 'localhost', port: 8000, debug: false }, null, 2),
  'data/notes.txt': 'Wartungsnotizen:\n- Server überprüfen\n- Logs durchsehen\n- Backup testen\n',
};

// Build a tar archive manually (simple tar format)
function createTar(files) {
  const entries = [];
  for (const [name, content] of Object.entries(files)) {
    const data = Buffer.from(content, 'utf-8');
    const header = Buffer.alloc(512);
    
    // name (100 bytes)
    header.write(name, 0, 'ascii');
    // mode (8 bytes, octal)
    header.write('0000644\0', 100, 'ascii');
    // uid (8 bytes)
    header.write('0001000\0', 108, 'ascii');
    // gid (8 bytes)
    header.write('0001000\0', 116, 'ascii');
    // size (12 bytes, octal)
    header.write(data.length.toString(8).padStart(11, '0') + '\0', 124, 'ascii');
    // mtime (12 bytes)
    header.write('00000000000\0', 136, 'ascii');
    // typeflag (1 byte) - '0' for regular file
    header.write('0', 156, 'ascii');
    // magic (ustar)
    header.write('ustar\0', 257, 'ascii');
    // version
    header.write('00', 263, 'ascii');
    
    // checksum: sum of all bytes in header with checksum field as spaces
    header.write('        ', 148, 'ascii'); // 8 spaces
    let checksum = 0;
    for (let i = 0; i < 512; i++) {
      checksum += header[i];
    }
    header.write(checksum.toString(8).padStart(6, '0') + '\0 ', 148, 'ascii');
    
    entries.push(header, data);
    // Pad to 512-byte boundary
    const padding = (512 - (data.length % 512)) % 512;
    if (padding > 0) {
      entries.push(Buffer.alloc(padding));
    }
  }
  // End-of-archive: two 512-byte zero blocks
  entries.push(Buffer.alloc(1024));
  return Buffer.concat(entries);
}

const tarBuffer = createTar(files);
const tarGzBuffer = gzipSync(tarBuffer);

// Derive key
const rawKey = await argon2idRaw({ password: PASSWORD, salt: SALT_BYTES, ...KDF_PARAMS });
const key = await importAesGcmKey(rawKey);
rawKey.fill(0);

// Encrypt frame-by-frame (64KB chunks)
const STREAM_CHUNK = 64 * 1024;
const frames = [];
for (let i = 0; i < tarGzBuffer.length; i += STREAM_CHUNK) {
  const chunk = tarGzBuffer.subarray(i, Math.min(i + STREAM_CHUNK, tarGzBuffer.length));
  const nonce = randomBytes(12);
  const ciphertext = await aesGcmEncrypt(key, nonce, chunk);
  
  const frame = Buffer.alloc(4 + 12 + ciphertext.length);
  frame.writeUInt32BE(12 + ciphertext.length, 0);
  Buffer.from(nonce).copy(frame, 4);
  Buffer.from(ciphertext).copy(frame, 16);
  frames.push(frame);
}

const encBuffer = Buffer.concat(frames);
const outputPath = 'C:\\Users\\einma\\AppData\\Local\\Temp\\msm-test-backup.enc';
writeFileSync(outputPath, encBuffer);

console.log(JSON.stringify({
  path: outputPath,
  password: PASSWORD,
  saltBase64: SALT_BASE64,
  encSize: encBuffer.length,
  tarGzSize: tarGzBuffer.length,
  fileCount: Object.keys(files).length,
  files: Object.keys(files),
}));
