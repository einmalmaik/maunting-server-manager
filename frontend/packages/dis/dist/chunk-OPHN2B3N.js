import { KDF_SALT_LENGTH } from './chunk-BUFRR5PB.js';
import { bytesToBase64, base64ToBytes } from './chunk-JSKIWIEC.js';
import { getCryptoProvider, subtle } from './chunk-CYIGDF63.js';
import { DisError, DisInvalidArgumentError } from './chunk-MJO7IJZC.js';
import { argon2id } from 'hash-wasm';

var DEFAULT_KDF_PARAMS = Object.freeze({
  1: { memory: 65536, iterations: 3, parallelism: 4, hashLength: 32 },
  2: { memory: 131072, iterations: 3, parallelism: 4, hashLength: 32 }
});
var CURRENT_KDF_VERSION = 2;
function generateSalt() {
  const salt = new Uint8Array(KDF_SALT_LENGTH);
  getCryptoProvider().getRandomValues(salt);
  return bytesToBase64(salt);
}
async function hkdfStrengthen(argon2Output, options) {
  const baseKey = await subtle().importKey("raw", argon2Output, "HKDF", false, [
    "deriveBits"
  ]);
  const info = new TextEncoder().encode(options.info);
  const derivedBits = await subtle().deriveBits(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: options.hkdfSalt,
      info
    },
    baseKey,
    argon2Output.length * 8
  );
  return new Uint8Array(derivedBits);
}
async function deriveRawKey(password, saltBase64, options = {}) {
  const version = options.version ?? CURRENT_KDF_VERSION;
  const registry = options.params ?? DEFAULT_KDF_PARAMS;
  const params = registry[version];
  if (!params) {
    throw new DisError("UNSUPPORTED_KDF_VERSION", `Unknown KDF version: ${version}`);
  }
  if (typeof password !== "string" || password.length === 0) {
    throw new DisInvalidArgumentError("password must be a non-empty string");
  }
  const salt = base64ToBytes(saltBase64);
  const result = await argon2id({
    password,
    salt,
    parallelism: params.parallelism,
    iterations: params.iterations,
    memorySize: params.memory,
    hashLength: params.hashLength,
    outputType: "binary"
  });
  let argon2Bytes;
  if (result instanceof Uint8Array) {
    argon2Bytes = result;
  } else if (result instanceof ArrayBuffer) {
    argon2Bytes = new Uint8Array(result);
  } else {
    throw new DisError("KEY_DERIVATION_FAILED", "Unexpected Argon2id output type");
  }
  if (!options.strengthen) {
    return argon2Bytes;
  }
  try {
    return await hkdfStrengthen(argon2Bytes, options.strengthen);
  } finally {
    argon2Bytes.fill(0);
  }
}
async function importAesGcmKey(keyBytes) {
  return subtle().importKey(
    "raw",
    keyBytes,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"]
  );
}
async function deriveAesGcmKey(password, saltBase64, options = {}) {
  const keyBytes = await deriveRawKey(password, saltBase64, options);
  try {
    return await importAesGcmKey(keyBytes);
  } finally {
    keyBytes.fill(0);
  }
}
async function argon2idRaw(params) {
  if (typeof params.password !== "string" || params.password.length === 0) {
    throw new DisInvalidArgumentError("password must be a non-empty string");
  }
  const result = await argon2id({
    password: params.password,
    salt: params.salt,
    parallelism: params.parallelism,
    iterations: params.iterations,
    memorySize: params.memorySize,
    hashLength: params.hashLength,
    outputType: "binary"
  });
  return new Uint8Array(result);
}
async function deriveHkdfSha256Bits(ikm, options) {
  const baseKey = await subtle().importKey(
    "raw",
    ikm,
    { name: "HKDF" },
    false,
    ["deriveBits"]
  );
  const bits = await subtle().deriveBits(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: options.salt ?? new Uint8Array(0),
      info: options.info
    },
    baseKey,
    options.lengthBits ?? 256
  );
  return new Uint8Array(bits);
}
async function deriveHkdfAesGcmKey(ikm, options) {
  const baseKey = await subtle().importKey(
    "raw",
    ikm,
    "HKDF",
    false,
    ["deriveKey"]
  );
  return subtle().deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: options.salt ?? new Uint8Array(0),
      info: options.info
    },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false,
    options.usages ?? ["encrypt", "decrypt"]
  );
}

export { CURRENT_KDF_VERSION, DEFAULT_KDF_PARAMS, argon2idRaw, deriveAesGcmKey, deriveHkdfAesGcmKey, deriveHkdfSha256Bits, deriveRawKey, generateSalt, importAesGcmKey };
//# sourceMappingURL=chunk-OPHN2B3N.js.map
//# sourceMappingURL=chunk-OPHN2B3N.js.map