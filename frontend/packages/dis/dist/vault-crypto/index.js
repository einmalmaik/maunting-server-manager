import { generateRsaOaepKeyPair, exportJwk, importRsaOaepPublicKey, importRsaOaepPrivateKey, rsaOaepEncrypt, rsaOaepDecrypt } from '../chunk-KNCZMIZA.js';
import { generateAesGcmKeyJwk, importAesGcmKeyFromJwk, createWrappedUserKey, createDeterministicWrappedUserKey, unwrapUserKey, unwrapUserKeyBytes, rotateWrappedKey } from '../chunk-3DQPQCAR.js';
import { generatePQKeyPair } from '../chunk-T3IV7SHD.js';
import '../chunk-3HCT6A2P.js';
import { SecureBuffer } from '../chunk-RTAJJZKO.js';
import { DEFAULT_KDF_PARAMS, CURRENT_KDF_VERSION, generateSalt, deriveRawKey, importAesGcmKey } from '../chunk-OPHN2B3N.js';
import { VAULT_ITEM_ENVELOPE_V1_PREFIX, encryptVaultEntry, decryptVaultEntryForMigration, decryptVaultEntry, VAULT_ITEM_ENVELOPE_SPEC } from '../chunk-JVFP2GAO.js';
import { encryptString, encryptBytes, decryptString, decryptBytes } from '../chunk-U65A4HIY.js';
import '../chunk-BUFRR5PB.js';
export { base64ToBytes, bytesToBase64 } from '../chunk-JSKIWIEC.js';
import '../chunk-CYIGDF63.js';
import { parseEnvelope } from '../chunk-SCHZI6YY.js';
import '../chunk-MJO7IJZC.js';

// src/vault-crypto/index.ts
var CURRENT_KDF_VERSION2 = CURRENT_KDF_VERSION;
var KDF_PARAMS = { ...DEFAULT_KDF_PARAMS };
var VAULT_ITEM_ENVELOPE_V1_PREFIX2 = VAULT_ITEM_ENVELOPE_V1_PREFIX;
var DEVICE_KEY_HKDF_INFO = "SINGRA_DEVICE_KEY_V1";
var VERIFICATION_CONSTANT_V3 = "SINGRA_VAULT_VERIFY_V3";
var ENCRYPTED_CATEGORY_PREFIX = "enc:cat:v1:";
var USK_V1_PREFIX = "usk-v1:";
var _legacyDecryptCount = 0;
function generateSalt2() {
  return generateSalt();
}
function strengthenOptions(deviceKey) {
  return deviceKey ? { hkdfSalt: deviceKey, info: DEVICE_KEY_HKDF_INFO } : void 0;
}
async function deriveRawKey2(masterPassword, saltBase64, kdfVersion = CURRENT_KDF_VERSION2, deviceKey) {
  return deriveRawKey(masterPassword, saltBase64, {
    version: kdfVersion,
    strengthen: strengthenOptions(deviceKey)
  });
}
async function deriveRawKeySecure(masterPassword, saltBase64, kdfVersion = CURRENT_KDF_VERSION2, deviceKey) {
  const rawBytes = await deriveRawKey2(masterPassword, saltBase64, kdfVersion, deviceKey);
  const secure = SecureBuffer.fromBytes(rawBytes);
  rawBytes.fill(0);
  return secure;
}
async function deriveKey(masterPassword, saltBase64, kdfVersion = CURRENT_KDF_VERSION2, deviceKey) {
  const keyBytes = await deriveRawKey2(masterPassword, saltBase64, kdfVersion, deviceKey);
  try {
    return await importMasterKey(keyBytes);
  } finally {
    keyBytes.fill(0);
  }
}
async function importMasterKey(keyBytes) {
  return importAesGcmKey(keyBytes);
}
async function encrypt(plaintext, key, aad) {
  return encryptString(plaintext, key, aad);
}
async function encryptBytes2(plaintextBytes, key, aad) {
  return encryptBytes(plaintextBytes, key, aad);
}
async function decrypt(encryptedBase64, key, aad) {
  return decryptString(encryptedBase64, key, aad);
}
async function decryptBytes2(encryptedBase64, key, aad) {
  return decryptBytes(encryptedBase64, key, aad);
}
async function encryptVaultItem(data, key, entryId) {
  return encryptVaultEntry(data, key, entryId);
}
async function decryptVaultItem(encryptedData, key, entryId, options = {}) {
  if (options.allowLegacyNoAadFallback) {
    const result = await decryptVaultEntryForMigration(encryptedData, key, entryId);
    if (result.legacyNoAadFallbackUsed) {
      console.warn(`Legacy entry without AAD detected: ${entryId}`);
      _legacyDecryptCount++;
    }
    return result.data;
  }
  return await decryptVaultEntry(encryptedData, key, entryId);
}
async function decryptVaultItemForMigration(encryptedData, key, entryId) {
  const result = await decryptVaultEntryForMigration(encryptedData, key, entryId);
  if (result.legacyNoAadFallbackUsed) {
    console.warn(`Legacy entry without AAD detected: ${entryId}`);
    _legacyDecryptCount++;
  }
  return {
    data: result.data,
    legacyEnvelopeUsed: result.legacyEnvelopeUsed,
    legacyNoAadFallbackUsed: result.legacyNoAadFallbackUsed
  };
}
function isCurrentVaultItemEnvelope(encryptedData) {
  return parseEnvelope(VAULT_ITEM_ENVELOPE_SPEC, encryptedData).version === 1;
}
async function createVerificationHash(key) {
  const encrypted = await encrypt(VERIFICATION_CONSTANT_V3, key);
  return `v3:${encrypted}`;
}
async function verifyKey(verificationHash, key) {
  try {
    if (verificationHash.startsWith("v3:")) {
      const encrypted = verificationHash.slice(3);
      const decrypted2 = await decrypt(encrypted, key);
      return decrypted2 === VERIFICATION_CONSTANT_V3;
    }
    if (verificationHash.startsWith("v2:")) {
      const parts = verificationHash.split(":");
      if (parts.length !== 3) {
        return false;
      }
      const challenge = parts[1];
      const encryptedChallenge = parts[2];
      const decrypted2 = await decrypt(encryptedChallenge, key);
      return decrypted2 === challenge;
    }
    const decrypted = await decrypt(verificationHash, key);
    return decrypted === "SINGRA_PW_VERIFICATION";
  } catch {
    return false;
  }
}
async function attemptKdfUpgrade(masterPassword, saltBase64, currentVersion, deviceKey, encryptedUserKey, existingKdfOutputBytes) {
  if (currentVersion >= CURRENT_KDF_VERSION2) {
    return { upgraded: false, activeVersion: currentVersion };
  }
  try {
    if (encryptedUserKey) {
      const ownedOldBytes = existingKdfOutputBytes ? null : await deriveRawKey2(masterPassword, saltBase64, currentVersion, deviceKey);
      const oldKdfOutputBytes = existingKdfOutputBytes ?? ownedOldBytes;
      const newKdfOutputBytes = await deriveRawKey2(
        masterPassword,
        saltBase64,
        CURRENT_KDF_VERSION2,
        deviceKey
      );
      try {
        const newEncryptedUserKey = await rewrapUserKey(
          encryptedUserKey,
          oldKdfOutputBytes,
          newKdfOutputBytes
        );
        const newUserKey = await unwrapUserKey2(newEncryptedUserKey, newKdfOutputBytes);
        const newVerifier2 = await createVerificationHash(newUserKey);
        return {
          upgraded: true,
          newVerifier: newVerifier2,
          newEncryptedUserKey,
          activeVersion: CURRENT_KDF_VERSION2
        };
      } finally {
        ownedOldBytes?.fill(0);
        newKdfOutputBytes.fill(0);
      }
    }
    const newKey = await deriveKey(masterPassword, saltBase64, CURRENT_KDF_VERSION2, deviceKey);
    const oldKey = await deriveKey(masterPassword, saltBase64, currentVersion, deviceKey);
    const newVerifier = await createVerificationHash(newKey);
    return { upgraded: true, newKey, oldKey, newVerifier, activeVersion: CURRENT_KDF_VERSION2 };
  } catch (err) {
    console.warn(
      `KDF upgrade from v${currentVersion} to v${CURRENT_KDF_VERSION2} failed (likely OOM), staying on v${currentVersion}:`,
      err
    );
    return { upgraded: false, activeVersion: currentVersion };
  }
}
async function reEncryptString(encryptedBase64, oldKey, newKey, aad) {
  let plaintext;
  if (aad) {
    try {
      plaintext = await decrypt(encryptedBase64, oldKey, aad);
    } catch {
      plaintext = await decrypt(encryptedBase64, oldKey);
    }
  } else {
    plaintext = await decrypt(encryptedBase64, oldKey);
  }
  return encrypt(plaintext, newKey, aad);
}
async function reEncryptVault(items, categories, oldKey, newKey) {
  const itemUpdates = [];
  for (const item of items) {
    try {
      const plaintext = await decryptVaultItem(item.encrypted_data, oldKey, item.id, {
        allowLegacyNoAadFallback: true
      });
      const newEncrypted = await encryptVaultItem(plaintext, newKey, item.id);
      itemUpdates.push({ id: item.id, encrypted_data: newEncrypted });
    } catch (err) {
      throw new Error(`Failed to re-encrypt vault item ${item.id}: ${err}`);
    }
  }
  const categoryUpdates = [];
  for (const cat of categories) {
    let newName = cat.name;
    let newIcon = cat.icon;
    let newColor = cat.color;
    let changed = false;
    if (cat.name.startsWith(ENCRYPTED_CATEGORY_PREFIX)) {
      try {
        const encPart = cat.name.slice(ENCRYPTED_CATEGORY_PREFIX.length);
        const reEncrypted = await reEncryptString(encPart, oldKey, newKey);
        newName = `${ENCRYPTED_CATEGORY_PREFIX}${reEncrypted}`;
        changed = true;
      } catch (err) {
        throw new Error(`Failed to re-encrypt category name ${cat.id}: ${err}`);
      }
    }
    if (cat.icon && cat.icon.startsWith(ENCRYPTED_CATEGORY_PREFIX)) {
      try {
        const encPart = cat.icon.slice(ENCRYPTED_CATEGORY_PREFIX.length);
        const reEncrypted = await reEncryptString(encPart, oldKey, newKey);
        newIcon = `${ENCRYPTED_CATEGORY_PREFIX}${reEncrypted}`;
        changed = true;
      } catch (err) {
        throw new Error(`Failed to re-encrypt category icon ${cat.id}: ${err}`);
      }
    }
    if (cat.color && cat.color.startsWith(ENCRYPTED_CATEGORY_PREFIX)) {
      try {
        const encPart = cat.color.slice(ENCRYPTED_CATEGORY_PREFIX.length);
        const reEncrypted = await reEncryptString(encPart, oldKey, newKey);
        newColor = `${ENCRYPTED_CATEGORY_PREFIX}${reEncrypted}`;
        changed = true;
      } catch (err) {
        throw new Error(`Failed to re-encrypt category color ${cat.id}: ${err}`);
      }
    }
    if (changed) {
      categoryUpdates.push({ id: cat.id, name: newName, icon: newIcon, color: newColor });
    }
  }
  const legacyFound = _legacyDecryptCount;
  _legacyDecryptCount = 0;
  return {
    itemsReEncrypted: itemUpdates.length,
    categoriesReEncrypted: categoryUpdates.length,
    itemUpdates,
    categoryUpdates,
    legacyItemsFound: legacyFound
  };
}
function clearReferences(data) {
  if (data.title) data.title = "";
  if (data.websiteUrl) data.websiteUrl = "";
  if (data.itemType) data.itemType = "password";
  if (typeof data.isFavorite === "boolean") data.isFavorite = false;
  if (typeof data.categoryId !== "undefined") data.categoryId = null;
  if (data.username) data.username = "";
  if (data.password) data.password = "";
  if (data.notes) data.notes = "";
  if (data.totpSecret) data.totpSecret = "";
  if (data.totpIssuer) data.totpIssuer = "";
  if (data.totpLabel) data.totpLabel = "";
  if (data.customFields) {
    Object.keys(data.customFields).forEach((key) => {
      data.customFields[key] = "";
    });
  }
}
var secureClear = clearReferences;
async function generateRSAKeyPair() {
  return generateRsaOaepKeyPair();
}
async function exportPublicKey(key) {
  return exportJwk(key);
}
async function importPublicKey(jwk) {
  return importRsaOaepPublicKey(jwk);
}
async function importPrivateKey(jwk) {
  return importRsaOaepPrivateKey(jwk);
}
async function exportPrivateKey(key) {
  return exportJwk(key);
}
async function encryptRSA(plaintext, publicKey) {
  return rsaOaepEncrypt(plaintext, publicKey);
}
async function decryptRSA(ciphertextBase64, privateKey) {
  return rsaOaepDecrypt(ciphertextBase64, privateKey);
}
async function generateUserKeyPair(masterPassword, version = 2) {
  if (version === 1) {
    const keyPair = await generateRsaOaepKeyPair();
    const publicKey = JSON.stringify(await exportJwk(keyPair.publicKey));
    const privateKey = JSON.stringify(await exportJwk(keyPair.privateKey));
    const salt2 = generateSalt2();
    const kdfVersion2 = CURRENT_KDF_VERSION2;
    const key2 = await deriveKey(masterPassword, salt2, kdfVersion2);
    const encryptedPrivateKey2 = await encrypt(privateKey, key2);
    return {
      publicKey,
      encryptedPrivateKey: `${kdfVersion2}:${salt2}:${encryptedPrivateKey2}`
    };
  }
  const rsaKeyPair = await generateRsaOaepKeyPair();
  const pqKeyPair = generatePQKeyPair();
  const { publicKey: pqPublicKeyBase64, secretKey: pqSecretKeyBase64 } = pqKeyPair;
  const rsaPublicKey = JSON.stringify(await exportJwk(rsaKeyPair.publicKey));
  const rsaPrivateKey = JSON.stringify(await exportJwk(rsaKeyPair.privateKey));
  const salt = generateSalt2();
  const kdfVersion = CURRENT_KDF_VERSION2;
  const key = await deriveKey(masterPassword, salt, kdfVersion);
  const encryptedRsaKey = await encrypt(rsaPrivateKey, key);
  const encryptedPqKey = await encrypt(pqSecretKeyBase64, key);
  const encryptedPrivateKey = `pq-v2:${kdfVersion}:${salt}:${encryptedRsaKey}:${encryptedPqKey}`;
  return { publicKey: rsaPublicKey, encryptedPrivateKey, pqPublicKey: pqPublicKeyBase64 };
}
async function migrateToHybridKeyPair(encryptedPrivateKey, masterPassword) {
  try {
    if (encryptedPrivateKey.startsWith("pq-v2:")) {
      return null;
    }
    const parts = encryptedPrivateKey.split(":");
    let kdfVersion = 1;
    let salt;
    let encryptedData;
    if (parts.length === 2) {
      salt = parts[0];
      encryptedData = parts[1];
    } else if (parts.length === 3) {
      kdfVersion = parseInt(parts[0], 10);
      salt = parts[1];
      encryptedData = parts[2];
    } else {
      throw new Error("Invalid encrypted private key format");
    }
    const key = await deriveKey(masterPassword, salt, kdfVersion);
    const rsaPrivateKey = await decrypt(encryptedData, key);
    const rsaPrivateKeyJwk = JSON.parse(rsaPrivateKey);
    const rsaPublicKeyJwk = {
      ...rsaPrivateKeyJwk,
      d: void 0,
      dp: void 0,
      dq: void 0,
      p: void 0,
      q: void 0,
      qi: void 0,
      key_ops: ["encrypt"]
    };
    const rsaPublicKey = JSON.stringify(rsaPublicKeyJwk);
    const pqKeyPair = generatePQKeyPair();
    const { publicKey: pqPublicKey, secretKey: pqSecretKey } = pqKeyPair;
    const newSalt = generateSalt2();
    const newKdfVersion = CURRENT_KDF_VERSION2;
    const newKey = await deriveKey(masterPassword, newSalt, newKdfVersion);
    const encryptedRsaKey = await encrypt(rsaPrivateKey, newKey);
    const encryptedPqKey = await encrypt(pqSecretKey, newKey);
    const hybridEncryptedKey = `pq-v2:${newKdfVersion}:${newSalt}:${encryptedRsaKey}:${encryptedPqKey}`;
    return { publicKey: rsaPublicKey, encryptedPrivateKey: hybridEncryptedKey, pqPublicKey };
  } catch (err) {
    console.error("Failed to migrate to hybrid key pair:", err);
    return null;
  }
}
async function generateSharedKey() {
  return generateAesGcmKeyJwk();
}
async function encryptWithSharedKey(data, sharedKey, aad) {
  const key = await importAesGcmKeyFromJwk(sharedKey, ["encrypt"]);
  return encrypt(JSON.stringify(data), key, aad);
}
async function decryptWithSharedKey(encryptedData, sharedKey, aad, options = {}) {
  const key = await importAesGcmKeyFromJwk(sharedKey, ["decrypt"]);
  let json;
  if (aad) {
    try {
      json = await decrypt(encryptedData, key, aad);
    } catch {
      if (!options.allowLegacyNoAadFallback) {
        throw new Error("Shared item decryption failed with the required AAD context.");
      }
      _legacyDecryptCount++;
      json = await decrypt(encryptedData, key);
    }
  } else {
    json = await decrypt(encryptedData, key);
  }
  return JSON.parse(json);
}
async function createEncryptedUserKey(kdfOutputBytes) {
  return createWrappedUserKey(kdfOutputBytes);
}
async function migrateToUserKey(kdfOutputBytes) {
  return createDeterministicWrappedUserKey(kdfOutputBytes);
}
async function unwrapUserKey2(encryptedUserKey, kdfOutputBytes) {
  return unwrapUserKey(encryptedUserKey, kdfOutputBytes);
}
async function unwrapUserKeyBytes2(encryptedUserKey, kdfOutputBytes) {
  return unwrapUserKeyBytes(encryptedUserKey, kdfOutputBytes);
}
async function rewrapUserKey(encryptedUserKey, oldKdfOutputBytes, newKdfOutputBytes) {
  return rotateWrappedKey(encryptedUserKey, oldKdfOutputBytes, newKdfOutputBytes);
}
async function wrapPrivateKeyWithUserKey(privateKeyMaterial, userKey) {
  const enc = await encrypt(privateKeyMaterial, userKey);
  return `${USK_V1_PREFIX}${enc}`;
}
async function unwrapPrivateKeyWithUserKey(wrappedKey, userKey) {
  if (!wrappedKey.startsWith(USK_V1_PREFIX)) {
    throw new Error("unwrapPrivateKeyWithUserKey: unexpected format (missing usk-v1: prefix)");
  }
  return decrypt(wrappedKey.slice(USK_V1_PREFIX.length), userKey);
}
async function decryptPrivateKeyLegacy(encryptedPrivateKey, masterPassword, extractPqPart = false) {
  if (encryptedPrivateKey.startsWith("pq-v2:")) {
    const rest = encryptedPrivateKey.slice("pq-v2:".length);
    const colonIdx1 = rest.indexOf(":");
    const colonIdx2 = rest.indexOf(":", colonIdx1 + 1);
    const colonIdx3 = rest.indexOf(":", colonIdx2 + 1);
    if (colonIdx1 < 0 || colonIdx2 < 0 || colonIdx3 < 0) {
      throw new Error("decryptPrivateKeyLegacy: invalid pq-v2 format");
    }
    const kdfVersion2 = parseInt(rest.slice(0, colonIdx1), 10);
    const salt2 = rest.slice(colonIdx1 + 1, colonIdx2);
    const encRsaKey = rest.slice(colonIdx2 + 1, colonIdx3);
    const encPqKey = rest.slice(colonIdx3 + 1);
    const key2 = await deriveKey(masterPassword, salt2, kdfVersion2);
    return extractPqPart ? decrypt(encPqKey, key2) : decrypt(encRsaKey, key2);
  }
  const parts = encryptedPrivateKey.split(":");
  let kdfVersion = 1;
  let salt;
  let encData;
  if (parts.length === 2) {
    salt = parts[0];
    encData = parts[1];
  } else if (parts.length === 3) {
    kdfVersion = parseInt(parts[0], 10);
    salt = parts[1];
    encData = parts[2];
  } else {
    throw new Error(
      `decryptPrivateKeyLegacy: unrecognised format (${parts.length} colon-separated parts)`
    );
  }
  const key = await deriveKey(masterPassword, salt, kdfVersion);
  return decrypt(encData, key);
}
async function getDecryptedRsaPrivateKey(encryptedPrivateKey, userKey, masterPassword) {
  if (encryptedPrivateKey.startsWith(USK_V1_PREFIX)) {
    if (!userKey) {
      throw new Error("getDecryptedRsaPrivateKey: UserKey required for usk-v1 format");
    }
    return unwrapPrivateKeyWithUserKey(encryptedPrivateKey, userKey);
  }
  return decryptPrivateKeyLegacy(encryptedPrivateKey, masterPassword, false);
}
async function getDecryptedPqPrivateKey(encryptedPqPrivateKey, userKey, masterPassword) {
  if (encryptedPqPrivateKey.startsWith(USK_V1_PREFIX)) {
    if (!userKey) {
      throw new Error("getDecryptedPqPrivateKey: UserKey required for usk-v1 format");
    }
    return unwrapPrivateKeyWithUserKey(encryptedPqPrivateKey, userKey);
  }
  if (encryptedPqPrivateKey.startsWith("pq-v2:")) {
    return decryptPrivateKeyLegacy(encryptedPqPrivateKey, masterPassword, true);
  }
  return decryptPrivateKeyLegacy(encryptedPqPrivateKey, masterPassword, false);
}

export { CURRENT_KDF_VERSION2 as CURRENT_KDF_VERSION, KDF_PARAMS, VAULT_ITEM_ENVELOPE_V1_PREFIX2 as VAULT_ITEM_ENVELOPE_V1_PREFIX, attemptKdfUpgrade, clearReferences, createEncryptedUserKey, createVerificationHash, decrypt, decryptBytes2 as decryptBytes, decryptPrivateKeyLegacy, decryptRSA, decryptVaultItem, decryptVaultItemForMigration, decryptWithSharedKey, deriveKey, deriveRawKey2 as deriveRawKey, deriveRawKeySecure, encrypt, encryptBytes2 as encryptBytes, encryptRSA, encryptVaultItem, encryptWithSharedKey, exportPrivateKey, exportPublicKey, generateRSAKeyPair, generateSalt2 as generateSalt, generateSharedKey, generateUserKeyPair, getDecryptedPqPrivateKey, getDecryptedRsaPrivateKey, importMasterKey, importPrivateKey, importPublicKey, isCurrentVaultItemEnvelope, migrateToHybridKeyPair, migrateToUserKey, reEncryptString, reEncryptVault, rewrapUserKey, secureClear, unwrapPrivateKeyWithUserKey, unwrapUserKey2 as unwrapUserKey, unwrapUserKeyBytes2 as unwrapUserKeyBytes, verifyKey, wrapPrivateKeyWithUserKey };
//# sourceMappingURL=index.js.map
//# sourceMappingURL=index.js.map