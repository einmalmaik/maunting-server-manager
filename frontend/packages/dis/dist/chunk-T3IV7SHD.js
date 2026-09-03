import { ml_kem768 } from '@noble/post-quantum/ml-kem.js';

// src/post-quantum/index.ts
var SECURITY_STANDARD_VERSION = 1;
var HYBRID_VERSION = 4;
var VERSION_RSA_ONLY = 1;
var VERSION_HYBRID_LEGACY = 2;
var VERSION_HYBRID_STANDARD_V1 = 3;
var VERSION_HYBRID_STANDARD_V2 = 4;
var ML_KEM_768_CIPHERTEXT_SIZE = 1088;
var RSA_4096_CIPHERTEXT_SIZE = 512;
var AES_GCM_IV_SIZE = 12;
var AES_GCM_TAG_SIZE = 16;
var HYBRID_CIPHERTEXT_MIN_SIZE = 1 + ML_KEM_768_CIPHERTEXT_SIZE + RSA_4096_CIPHERTEXT_SIZE + AES_GCM_IV_SIZE + AES_GCM_TAG_SIZE;
var HYBRID_KDF_INFO_PREFIX = new TextEncoder().encode("Singra Vault-HybridKDF-v1:");
var HYBRID_KDF_INFO_V2 = new TextEncoder().encode("Singra Vault-HybridKDF-v2:");
function buildSharedKeyWrapAad(input) {
  const collectionId = requireNonEmptyAadPart(input.collectionId, "collectionId");
  const senderUserId = requireNonEmptyAadPart(input.senderUserId, "senderUserId");
  const recipientUserId = requireNonEmptyAadPart(input.recipientUserId, "recipientUserId");
  const keyVersion = requireNonEmptyAadPart(String(input.keyVersion), "keyVersion");
  return `sv:shared-key:v1:${collectionId}:${senderUserId}:${recipientUserId}:${keyVersion}`;
}
function generatePQKeyPair() {
  const seed = crypto.getRandomValues(new Uint8Array(64));
  const { publicKey, secretKey } = ml_kem768.keygen(seed);
  seed.fill(0);
  return {
    publicKey: uint8ArrayToBase64(publicKey),
    secretKey: uint8ArrayToBase64(secretKey)
  };
}
async function generateHybridKeyPair() {
  const pqKeys = generatePQKeyPair();
  const rsaKeyPair = await crypto.subtle.generateKey(
    {
      name: "RSA-OAEP",
      modulusLength: 4096,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256"
    },
    true,
    ["encrypt", "decrypt"]
  );
  const rsaPublicJwk = await crypto.subtle.exportKey("jwk", rsaKeyPair.publicKey);
  const rsaPrivateJwk = await crypto.subtle.exportKey("jwk", rsaKeyPair.privateKey);
  return {
    pqPublicKey: pqKeys.publicKey,
    pqSecretKey: pqKeys.secretKey,
    rsaPublicKey: JSON.stringify(rsaPublicJwk),
    rsaPrivateKey: JSON.stringify(rsaPrivateJwk)
  };
}
async function hybridEncrypt(plaintext, pqPublicKey, rsaPublicKey, aad) {
  const aesKeyBytes = crypto.getRandomValues(new Uint8Array(32));
  const pqPubKeyBytes = base64ToUint8Array(pqPublicKey);
  const { cipherText: pqCiphertext, sharedSecret: pqSharedSecret } = ml_kem768.encapsulate(pqPubKeyBytes);
  const rsaPubKeyJwk = JSON.parse(rsaPublicKey);
  const rsaPubKey = await crypto.subtle.importKey(
    "jwk",
    rsaPubKeyJwk,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"]
  );
  const rsaCiphertext = await crypto.subtle.encrypt(
    { name: "RSA-OAEP" },
    rsaPubKey,
    asBufferSource(aesKeyBytes)
  );
  const rsaCiphertextBytes = new Uint8Array(rsaCiphertext);
  const combinedKey = await deriveHybridCombinedKeyV2(
    pqSharedSecret,
    aesKeyBytes,
    rsaCiphertextBytes
  );
  const aesKey = await crypto.subtle.importKey(
    "raw",
    asBufferSource(combinedKey),
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"]
  );
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintextBytes = new TextEncoder().encode(plaintext);
  const aadBytes = aad ? new TextEncoder().encode(aad) : void 0;
  const gcmParams = { name: "AES-GCM", iv, tagLength: 128 };
  if (aadBytes) {
    gcmParams.additionalData = aadBytes;
  }
  const aesCiphertext = await crypto.subtle.encrypt(
    gcmParams,
    aesKey,
    plaintextBytes
  );
  aesKeyBytes.fill(0);
  pqSharedSecret.fill(0);
  combinedKey.fill(0);
  const aesCiphertextBytes = new Uint8Array(aesCiphertext);
  const totalLength = 1 + pqCiphertext.length + rsaCiphertextBytes.length + iv.length + aesCiphertextBytes.length;
  const combined = new Uint8Array(totalLength);
  let offset = 0;
  combined[offset++] = VERSION_HYBRID_STANDARD_V2;
  combined.set(pqCiphertext, offset);
  offset += pqCiphertext.length;
  combined.set(rsaCiphertextBytes, offset);
  offset += rsaCiphertextBytes.length;
  combined.set(iv, offset);
  offset += iv.length;
  combined.set(aesCiphertextBytes, offset);
  return uint8ArrayToBase64(combined);
}
async function hybridDecrypt(ciphertextBase64, pqSecretKey, rsaPrivateKey, aad) {
  return decryptHybridCiphertext(ciphertextBase64, pqSecretKey, rsaPrivateKey, false, aad);
}
async function legacyRsaDecrypt(ciphertext, rsaPrivateKey) {
  const rsaPrivKeyJwk = JSON.parse(rsaPrivateKey);
  const rsaPrivKey = await crypto.subtle.importKey(
    "jwk",
    rsaPrivKeyJwk,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["decrypt"]
  );
  const plaintextBytes = await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    rsaPrivKey,
    asBufferSource(ciphertext)
  );
  return new TextDecoder().decode(plaintextBytes);
}
async function hybridWrapKey(sharedKeyJwk, pqPublicKey, rsaPublicKey, aad) {
  return hybridEncrypt(sharedKeyJwk, pqPublicKey, rsaPublicKey, requireAad(aad, "hybridWrapKey"));
}
async function hybridUnwrapKey(wrappedKey, pqSecretKey, rsaPrivateKey, aad) {
  return hybridDecrypt(wrappedKey, pqSecretKey, rsaPrivateKey, requireAad(aad, "hybridUnwrapKey"));
}
function isHybridEncrypted(ciphertextBase64) {
  try {
    const combined = base64ToUint8Array(ciphertextBase64);
    const v = combined[0];
    return v === VERSION_HYBRID_LEGACY || v === VERSION_HYBRID_STANDARD_V1 || v === VERSION_HYBRID_STANDARD_V2;
  } catch {
    return false;
  }
}
function isCurrentStandardEncrypted(ciphertextBase64) {
  try {
    const combined = base64ToUint8Array(ciphertextBase64);
    return combined[0] === VERSION_HYBRID_STANDARD_V2;
  } catch {
    return false;
  }
}
async function migrateToHybrid(legacyCiphertext, rsaPrivateKey, pqSecretKey, pqPublicKey, rsaPublicKey, aad) {
  const combined = base64ToUint8Array(legacyCiphertext);
  const version = combined[0];
  if (version === VERSION_HYBRID_STANDARD_V2) {
    if (aad) {
      if (!pqSecretKey) {
        throw new Error("PQ secret key is required to verify current hybrid ciphertext AAD.");
      }
      await decryptHybridCiphertext(
        legacyCiphertext,
        pqSecretKey,
        rsaPrivateKey,
        false,
        aad
      );
    }
    return legacyCiphertext;
  }
  let plaintext;
  if (version === VERSION_HYBRID_STANDARD_V1) {
    if (!pqSecretKey) {
      throw new Error("PQ secret key is required to migrate v0x03 ciphertext.");
    }
    plaintext = await decryptHybridCiphertext(
      legacyCiphertext,
      pqSecretKey,
      rsaPrivateKey,
      true,
      // allowLegacyFormats for internal re-encryption
      aad
    );
  } else if (version === VERSION_RSA_ONLY) {
    plaintext = await legacyRsaDecrypt(combined.slice(1), rsaPrivateKey);
  } else if (version === VERSION_HYBRID_LEGACY) {
    if (!pqSecretKey) {
      throw new Error("PQ secret key is required to migrate legacy hybrid ciphertext.");
    }
    plaintext = await decryptHybridCiphertext(
      legacyCiphertext,
      pqSecretKey,
      rsaPrivateKey,
      true,
      aad
    );
  } else {
    plaintext = await legacyRsaDecrypt(combined, rsaPrivateKey);
  }
  return hybridEncrypt(plaintext, pqPublicKey, rsaPublicKey, aad);
}
function uint8ArrayToBase64(bytes) {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}
function base64ToUint8Array(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
function concatUint8Arrays(first, second) {
  const combined = new Uint8Array(first.length + second.length);
  combined.set(first, 0);
  combined.set(second, first.length);
  return combined;
}
async function deriveHybridCombinedKey(pqSharedSecret, aesKeyBytes, rsaCiphertext) {
  const baseKey = await crypto.subtle.importKey(
    "raw",
    asBufferSource(pqSharedSecret),
    "HKDF",
    false,
    ["deriveBits"]
  );
  const info = concatUint8Arrays(HYBRID_KDF_INFO_PREFIX, rsaCiphertext);
  try {
    const derivedBits = await crypto.subtle.deriveBits(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: asBufferSource(aesKeyBytes),
        info: asBufferSource(info)
      },
      baseKey,
      256
    );
    return new Uint8Array(derivedBits);
  } finally {
    info.fill(0);
  }
}
async function deriveHybridCombinedKeyV2(pqSharedSecret, aesKeyBytes, rsaCiphertext) {
  const ikm = concatUint8Arrays(pqSharedSecret, aesKeyBytes);
  const baseKey = await crypto.subtle.importKey(
    "raw",
    asBufferSource(ikm),
    "HKDF",
    false,
    ["deriveBits"]
  );
  const info = concatUint8Arrays(HYBRID_KDF_INFO_V2, rsaCiphertext);
  try {
    const derivedBits = await crypto.subtle.deriveBits(
      {
        name: "HKDF",
        hash: "SHA-256",
        salt: asBufferSource(new Uint8Array(32)),
        // zero-byte salt (NIST-recommended)
        info: asBufferSource(info)
      },
      baseKey,
      256
    );
    return new Uint8Array(derivedBits);
  } finally {
    ikm.fill(0);
    info.fill(0);
  }
}
async function decryptHybridCiphertext(ciphertextBase64, pqSecretKey, rsaPrivateKey, allowLegacyFormats, aad) {
  const combined = base64ToUint8Array(ciphertextBase64);
  const version = combined[0];
  if (!allowLegacyFormats && version !== VERSION_HYBRID_STANDARD_V1 && version !== VERSION_HYBRID_STANDARD_V2) {
    throw new Error("Security Standard v1 requires hybrid ciphertext version 3 or 4.");
  }
  if (version === VERSION_RSA_ONLY) {
    if (!allowLegacyFormats) {
      throw new Error("RSA-only ciphertext is blocked by Security Standard v1.");
    }
    return legacyRsaDecrypt(combined.slice(1), rsaPrivateKey);
  }
  if (version !== VERSION_HYBRID_STANDARD_V2 && version !== VERSION_HYBRID_STANDARD_V1 && version !== VERSION_HYBRID_LEGACY) {
    throw new Error(`Unsupported encryption version: ${version}`);
  }
  const { pqCiphertext, rsaCiphertext, iv, aesCiphertext } = parseHybridCiphertext(combined);
  const pqSecretKeyBytes = base64ToUint8Array(pqSecretKey);
  const pqSharedSecret = ml_kem768.decapsulate(pqCiphertext, pqSecretKeyBytes);
  const rsaPrivKeyJwk = JSON.parse(rsaPrivateKey);
  const rsaPrivKey = await crypto.subtle.importKey(
    "jwk",
    rsaPrivKeyJwk,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["decrypt"]
  );
  const aesKeyBytes = new Uint8Array(await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    rsaPrivKey,
    asBufferSource(rsaCiphertext)
  ));
  let combinedKey;
  if (version === VERSION_HYBRID_STANDARD_V2) {
    combinedKey = await deriveHybridCombinedKeyV2(
      pqSharedSecret,
      aesKeyBytes,
      rsaCiphertext
    );
  } else {
    combinedKey = await deriveHybridCombinedKey(
      pqSharedSecret,
      aesKeyBytes,
      rsaCiphertext
    );
  }
  const aesKey = await crypto.subtle.importKey(
    "raw",
    asBufferSource(combinedKey),
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );
  try {
    const aadBytes = aad ? new TextEncoder().encode(aad) : void 0;
    const gcmParams = { name: "AES-GCM", iv: asBufferSource(iv), tagLength: 128 };
    if (aadBytes) {
      gcmParams.additionalData = aadBytes;
    }
    const plaintextBytes = await crypto.subtle.decrypt(
      gcmParams,
      aesKey,
      asBufferSource(aesCiphertext)
    );
    return new TextDecoder().decode(plaintextBytes);
  } finally {
    aesKeyBytes.fill(0);
    pqSharedSecret.fill(0);
    combinedKey.fill(0);
  }
}
function parseHybridCiphertext(combined) {
  const version = combined[0];
  if (combined.length < HYBRID_CIPHERTEXT_MIN_SIZE || version !== VERSION_HYBRID_LEGACY && version !== VERSION_HYBRID_STANDARD_V1 && version !== VERSION_HYBRID_STANDARD_V2) {
    throw new Error("Invalid hybrid ciphertext format.");
  }
  let offset = 1;
  const pqCiphertext = combined.slice(offset, offset + ML_KEM_768_CIPHERTEXT_SIZE);
  offset += ML_KEM_768_CIPHERTEXT_SIZE;
  const rsaCiphertext = combined.slice(offset, offset + RSA_4096_CIPHERTEXT_SIZE);
  offset += RSA_4096_CIPHERTEXT_SIZE;
  const iv = combined.slice(offset, offset + AES_GCM_IV_SIZE);
  offset += AES_GCM_IV_SIZE;
  const aesCiphertext = combined.slice(offset);
  if (pqCiphertext.length !== ML_KEM_768_CIPHERTEXT_SIZE || rsaCiphertext.length !== RSA_4096_CIPHERTEXT_SIZE || iv.length !== AES_GCM_IV_SIZE || aesCiphertext.length < AES_GCM_TAG_SIZE) {
    throw new Error("Invalid hybrid ciphertext format.");
  }
  return { pqCiphertext, rsaCiphertext, iv, aesCiphertext };
}
function requireAad(aad, operation) {
  if (typeof aad !== "string" || aad.trim().length === 0) {
    throw new Error(`${operation} requires non-empty AAD for wrapped-key context binding.`);
  }
  return aad;
}
function requireNonEmptyAadPart(value, label) {
  const normalized = value.trim();
  if (!normalized) {
    throw new Error(`Missing AAD component: ${label}`);
  }
  if (normalized.includes(":")) {
    throw new Error(`AAD component must not contain ':': ${label}`);
  }
  return normalized;
}
function asBufferSource(bytes) {
  return bytes;
}

export { HYBRID_VERSION, SECURITY_STANDARD_VERSION, buildSharedKeyWrapAad, generateHybridKeyPair, generatePQKeyPair, hybridDecrypt, hybridEncrypt, hybridUnwrapKey, hybridWrapKey, isCurrentStandardEncrypted, isHybridEncrypted, migrateToHybrid };
//# sourceMappingURL=chunk-T3IV7SHD.js.map
//# sourceMappingURL=chunk-T3IV7SHD.js.map