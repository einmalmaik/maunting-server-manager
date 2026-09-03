import { bytesToBase64, bytesToBase64Url, bytesToHex, utf8ToBytes, base64ToBytes } from './chunk-JSKIWIEC.js';
import { subtle } from './chunk-CYIGDF63.js';
import { DisIntegrityError } from './chunk-MJO7IJZC.js';

// src/integrity/index.ts
async function sha256Bytes(data) {
  const digest = await subtle().digest("SHA-256", data);
  return new Uint8Array(digest);
}
async function sha256Base64(data) {
  return bytesToBase64(await sha256Bytes(data));
}
async function sha256Base64Url(data) {
  return bytesToBase64Url(await sha256Bytes(data));
}
async function sha256Hex(data) {
  return bytesToHex(await sha256Bytes(data));
}
async function sha1Hex(data) {
  const digest = await subtle().digest("SHA-1", data);
  return bytesToHex(new Uint8Array(digest));
}
async function importHmacSha256Key(keyBytes, usages = ["sign", "verify"]) {
  return subtle().importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    usages
  );
}
async function hmacSha256WithKey(key, data) {
  const sig = await subtle().sign("HMAC", key, data);
  return new Uint8Array(sig);
}
async function hmacSha256(keyBytes, data) {
  const key = await importHmacSha256Key(keyBytes, ["sign"]);
  return hmacSha256WithKey(key, data);
}
async function sha256StringBase64(data) {
  return sha256Base64(utf8ToBytes(data));
}
async function sha256JsonBase64(value) {
  return sha256StringBase64(JSON.stringify(value));
}
function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a[i] ^ b[i];
  }
  return result === 0;
}
function constantTimeEqualBase64(a, b) {
  return constantTimeEqual(base64ToBytes(a), base64ToBytes(b));
}
async function verifyPayloadIntegrity(data, expectedBase64) {
  const actual = await sha256Base64(data);
  if (!constantTimeEqualBase64(actual, expectedBase64)) {
    throw new DisIntegrityError();
  }
}

export { constantTimeEqual, constantTimeEqualBase64, hmacSha256, hmacSha256WithKey, importHmacSha256Key, sha1Hex, sha256Base64, sha256Base64Url, sha256Bytes, sha256Hex, sha256JsonBase64, sha256StringBase64, verifyPayloadIntegrity };
//# sourceMappingURL=chunk-MPWYZXW7.js.map
//# sourceMappingURL=chunk-MPWYZXW7.js.map