import { AES_GCM_TAG_LENGTH, AES_GCM_IV_LENGTH } from './chunk-BUFRR5PB.js';
import { bytesToBase64, base64ToBytes, utf8ToBytes, bytesToUtf8 } from './chunk-JSKIWIEC.js';
import { getCryptoProvider, subtle } from './chunk-CYIGDF63.js';
import { DisInvalidArgumentError, DisDecryptionError } from './chunk-MJO7IJZC.js';

// src/aead/index.ts
function aad(value) {
  return value ? utf8ToBytes(value) : void 0;
}
async function encryptBytes(plaintextBytes, key, associatedData) {
  const iv = new Uint8Array(AES_GCM_IV_LENGTH);
  getCryptoProvider().getRandomValues(iv);
  const additionalData = aad(associatedData);
  let ciphertextBytes = null;
  let combined = null;
  try {
    const ciphertext = await subtle().encrypt(
      {
        name: "AES-GCM",
        iv,
        tagLength: AES_GCM_TAG_LENGTH,
        ...additionalData && { additionalData }
      },
      key,
      plaintextBytes
    );
    ciphertextBytes = new Uint8Array(ciphertext);
    combined = new Uint8Array(iv.length + ciphertextBytes.byteLength);
    combined.set(iv, 0);
    combined.set(ciphertextBytes, iv.length);
    return bytesToBase64(combined);
  } finally {
    iv.fill(0);
    additionalData?.fill(0);
    ciphertextBytes?.fill(0);
    combined?.fill(0);
  }
}
async function decryptBytes(encryptedBase64, key, associatedData) {
  const combined = base64ToBytes(encryptedBase64);
  if (combined.length <= AES_GCM_IV_LENGTH) {
    combined.fill(0);
    throw new DisInvalidArgumentError("Invalid encrypted data");
  }
  const iv = combined.slice(0, AES_GCM_IV_LENGTH);
  const ciphertext = combined.slice(AES_GCM_IV_LENGTH);
  const additionalData = aad(associatedData);
  try {
    const plaintext = await subtle().decrypt(
      {
        name: "AES-GCM",
        iv,
        tagLength: AES_GCM_TAG_LENGTH,
        ...additionalData && { additionalData }
      },
      key,
      ciphertext
    );
    return new Uint8Array(plaintext);
  } catch {
    throw new DisDecryptionError();
  } finally {
    combined.fill(0);
    iv.fill(0);
    ciphertext.fill(0);
    additionalData?.fill(0);
  }
}
async function importAesGcmRawKey(keyBytes, usages) {
  return subtle().importKey(
    "raw",
    keyBytes,
    { name: "AES-GCM" },
    false,
    usages
  );
}
async function generateAesGcmKey(usages = ["encrypt", "decrypt"], extractable = false) {
  return subtle().generateKey({ name: "AES-GCM", length: 256 }, extractable, usages);
}
async function aesGcmEncrypt(key, nonce, plaintext, associatedData) {
  const cryptoKey = key instanceof Uint8Array ? await importAesGcmRawKey(key, ["encrypt"]) : key;
  const ciphertext = await subtle().encrypt(
    {
      name: "AES-GCM",
      iv: nonce,
      tagLength: AES_GCM_TAG_LENGTH,
      ...associatedData && { additionalData: associatedData }
    },
    cryptoKey,
    plaintext
  );
  return new Uint8Array(ciphertext);
}
async function aesGcmDecrypt(key, nonce, ciphertext, associatedData) {
  const cryptoKey = key instanceof Uint8Array ? await importAesGcmRawKey(key, ["decrypt"]) : key;
  try {
    const plaintext = await subtle().decrypt(
      {
        name: "AES-GCM",
        iv: nonce,
        tagLength: AES_GCM_TAG_LENGTH,
        ...associatedData && { additionalData: associatedData }
      },
      cryptoKey,
      ciphertext
    );
    return new Uint8Array(plaintext);
  } catch {
    throw new DisDecryptionError();
  }
}
async function encryptString(plaintext, key, associatedData) {
  const plaintextBytes = utf8ToBytes(plaintext);
  try {
    return await encryptBytes(plaintextBytes, key, associatedData);
  } finally {
    plaintextBytes.fill(0);
  }
}
async function decryptString(encryptedBase64, key, associatedData) {
  let plaintextBytes = null;
  try {
    plaintextBytes = await decryptBytes(encryptedBase64, key, associatedData);
    return bytesToUtf8(plaintextBytes);
  } finally {
    plaintextBytes?.fill(0);
  }
}

export { aesGcmDecrypt, aesGcmEncrypt, decryptBytes, decryptString, encryptBytes, encryptString, generateAesGcmKey, importAesGcmRawKey };
//# sourceMappingURL=chunk-U65A4HIY.js.map
//# sourceMappingURL=chunk-U65A4HIY.js.map