import { randomBytes } from './chunk-3HCT6A2P.js';
import { importAesGcmKey } from './chunk-OPHN2B3N.js';
import { encryptBytes, decryptBytes, decryptString } from './chunk-U65A4HIY.js';
import { AES_KEY_LENGTH } from './chunk-BUFRR5PB.js';
import { base64ToBytes } from './chunk-JSKIWIEC.js';
import { subtle } from './chunk-CYIGDF63.js';
import { DisInvalidArgumentError } from './chunk-MJO7IJZC.js';

// src/key-management/index.ts
var DEFAULT_KEY_WRAP_SCHEME = {
  prefix: "usk-wrap-v2:",
  hkdfInfo: "singra-vault-wrap-v1"
};
async function deriveWrapKeyBytes(kdfOutputBytes, scheme) {
  const baseKey = await subtle().importKey("raw", kdfOutputBytes, "HKDF", false, [
    "deriveBits"
  ]);
  const bits = await subtle().deriveBits(
    {
      name: "HKDF",
      hash: "SHA-256",
      // Zero salt is correct: IKM is already high-entropy Argon2id output.
      salt: new Uint8Array(32),
      info: new TextEncoder().encode(scheme.hkdfInfo)
    },
    baseKey,
    256
  );
  return new Uint8Array(bits);
}
function generateContentKeyBytes() {
  return randomBytes(AES_KEY_LENGTH);
}
async function createWrappedUserKey(kdfOutputBytes, scheme = DEFAULT_KEY_WRAP_SCHEME) {
  const userKeyBytes = generateContentKeyBytes();
  let wrapKeyBytes = null;
  try {
    wrapKeyBytes = await deriveWrapKeyBytes(kdfOutputBytes, scheme);
    const wrapKey = await importAesGcmKey(wrapKeyBytes);
    const encryptedUserKey = `${scheme.prefix}${await encryptBytes(userKeyBytes, wrapKey)}`;
    const userKey = await importAesGcmKey(userKeyBytes);
    return { encryptedUserKey, userKey };
  } finally {
    userKeyBytes.fill(0);
    wrapKeyBytes?.fill(0);
  }
}
async function unwrapUserKeyBytes(encryptedUserKey, kdfOutputBytes, scheme = DEFAULT_KEY_WRAP_SCHEME) {
  let wrapKeyBytes = null;
  try {
    wrapKeyBytes = await deriveWrapKeyBytes(kdfOutputBytes, scheme);
    const wrapKey = await importAesGcmKey(wrapKeyBytes);
    if (encryptedUserKey.startsWith(scheme.prefix)) {
      return await decryptBytes(encryptedUserKey.slice(scheme.prefix.length), wrapKey);
    }
    const userKeyBase64 = await decryptString(encryptedUserKey, wrapKey);
    return base64ToBytes(userKeyBase64);
  } finally {
    wrapKeyBytes?.fill(0);
  }
}
async function unwrapUserKey(encryptedUserKey, kdfOutputBytes, scheme = DEFAULT_KEY_WRAP_SCHEME) {
  const userKeyBytes = await unwrapUserKeyBytes(encryptedUserKey, kdfOutputBytes, scheme);
  try {
    return await importAesGcmKey(userKeyBytes);
  } finally {
    userKeyBytes.fill(0);
  }
}
async function createDeterministicWrappedUserKey(kdfOutputBytes, scheme = DEFAULT_KEY_WRAP_SCHEME) {
  const userKeyBytes = new Uint8Array(kdfOutputBytes);
  let wrapKeyBytes = null;
  try {
    wrapKeyBytes = await deriveWrapKeyBytes(kdfOutputBytes, scheme);
    const wrapKey = await importAesGcmKey(wrapKeyBytes);
    const encryptedUserKey = `${scheme.prefix}${await encryptBytes(userKeyBytes, wrapKey)}`;
    const userKey = await importAesGcmKey(userKeyBytes);
    return { encryptedUserKey, userKey };
  } finally {
    userKeyBytes.fill(0);
    wrapKeyBytes?.fill(0);
  }
}
async function generateAesGcmKeyJwk() {
  const key = await subtle().generateKey({ name: "AES-GCM", length: 256 }, true, [
    "encrypt",
    "decrypt"
  ]);
  const jwk = await subtle().exportKey("jwk", key);
  return JSON.stringify(jwk);
}
async function importAesGcmKeyFromJwk(jwkString, usages) {
  const jwk = JSON.parse(jwkString);
  return subtle().importKey("jwk", jwk, { name: "AES-GCM", length: 256 }, false, [...usages]);
}
async function rotateWrappedKey(encryptedUserKey, oldKdfOutputBytes, newKdfOutputBytes, scheme = DEFAULT_KEY_WRAP_SCHEME) {
  if (!encryptedUserKey) {
    throw new DisInvalidArgumentError("encryptedUserKey is required");
  }
  const userKeyBytes = await unwrapUserKeyBytes(encryptedUserKey, oldKdfOutputBytes, scheme);
  let newWrapKeyBytes = null;
  try {
    newWrapKeyBytes = await deriveWrapKeyBytes(newKdfOutputBytes, scheme);
    const newWrapKey = await importAesGcmKey(newWrapKeyBytes);
    return `${scheme.prefix}${await encryptBytes(userKeyBytes, newWrapKey)}`;
  } finally {
    userKeyBytes.fill(0);
    newWrapKeyBytes?.fill(0);
  }
}

export { DEFAULT_KEY_WRAP_SCHEME, createDeterministicWrappedUserKey, createWrappedUserKey, generateAesGcmKeyJwk, generateContentKeyBytes, importAesGcmKeyFromJwk, rotateWrappedKey, unwrapUserKey, unwrapUserKeyBytes };
//# sourceMappingURL=chunk-3DQPQCAR.js.map
//# sourceMappingURL=chunk-3DQPQCAR.js.map