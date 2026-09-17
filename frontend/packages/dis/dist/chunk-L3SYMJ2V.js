import { zeroBuffers, SecureBuffer } from './chunk-RTAJJZKO.js';
import { deriveHkdfSha256Bits } from './chunk-OPHN2B3N.js';
import { aesGcmEncrypt, aesGcmDecrypt } from './chunk-U65A4HIY.js';
import { utf8ToBytes, bytesToBase64, base64ToBytes, concatBytes } from './chunk-JSKIWIEC.js';
import { subtle } from './chunk-CYIGDF63.js';
import { formatEnvelope, parseEnvelope } from './chunk-SCHZI6YY.js';
import { DisInvalidArgumentError, DisDecryptionError } from './chunk-MJO7IJZC.js';

// src/messaging/types.ts
var RATCHET_MESSAGE_V1_PREFIX = "sv-dr-msg-v1:";
var RATCHET_STATE_V1_PREFIX = "sv-dr-state-v1:";
var RATCHET_MESSAGE_V1_TAG = "sv-dr-msg-v1";
var DEFAULT_MAX_SKIPPED_KEYS = 1e3;
var DH_PUBLIC_KEY_LENGTH = 65;
var DH_PRIVATE_KEY_LENGTH = 138;
var RATCHET_KEY_LENGTH = 32;

// src/messaging/ratchet.ts
var ROOT_KDF_INFO = utf8ToBytes("sv-dr-root-v1");
var CHAIN_KDF_INFO = utf8ToBytes("sv-dr-chain-v1");
var MESSAGE_KDF_INFO = utf8ToBytes("sv-dr-msg-v1");
var ZERO_SALT = new Uint8Array(32);
var AAD_SEPARATOR = new Uint8Array([0]);
var EMPTY_BYTES = new Uint8Array(0);
var AES_KEY_BYTES = 32;
var GCM_NONCE_BYTES = 12;
var MESSAGE_MATERIAL_BYTES = AES_KEY_BYTES + GCM_NONCE_BYTES;
var ECDH_P256_ALGORITHM = { name: "ECDH", namedCurve: "P-256" };
var MESSAGE_ENVELOPE_SPEC = {
  currentPrefix: RATCHET_MESSAGE_V1_PREFIX,
  familyPrefix: "sv-dr-msg-",
  subject: "ratchet message"
};
var STATE_ENVELOPE_SPEC = {
  currentPrefix: RATCHET_STATE_V1_PREFIX,
  familyPrefix: "sv-dr-state-",
  subject: "ratchet state"
};
function cloneKeyPair(pair) {
  return {
    algorithm: pair.algorithm,
    publicKey: new Uint8Array(pair.publicKey),
    privateKey: new Uint8Array(pair.privateKey)
  };
}
function cloneState(state) {
  return {
    version: 1,
    dhSelf: cloneKeyPair(state.dhSelf),
    dhRemote: state.dhRemote === null ? null : new Uint8Array(state.dhRemote),
    rootKey: new Uint8Array(state.rootKey),
    sendingChainKey: state.sendingChainKey === null ? null : new Uint8Array(state.sendingChainKey),
    receivingChainKey: state.receivingChainKey === null ? null : new Uint8Array(state.receivingChainKey),
    sendCount: state.sendCount,
    receiveCount: state.receiveCount,
    previousSendCount: state.previousSendCount,
    skippedMessageKeys: state.skippedMessageKeys.map((entry) => ({
      dh: entry.dh,
      n: entry.n,
      messageKey: new Uint8Array(entry.messageKey)
    })),
    maxSkippedKeys: state.maxSkippedKeys,
    associatedData: state.associatedData === null ? null : new Uint8Array(state.associatedData)
  };
}
async function generateRatchetKeyPair() {
  const pair = await subtle().generateKey(ECDH_P256_ALGORITHM, true, ["deriveBits"]);
  const publicKey = new Uint8Array(await subtle().exportKey("raw", pair.publicKey));
  const privateKey = new Uint8Array(await subtle().exportKey("pkcs8", pair.privateKey));
  return { algorithm: "ECDH-P-256", publicKey, privateKey };
}
async function ecdhShared(privateKeyBytes, publicKeyBytes) {
  const privateKey = await subtle().importKey(
    "pkcs8",
    privateKeyBytes,
    ECDH_P256_ALGORITHM,
    false,
    ["deriveBits"]
  );
  const publicKey = await subtle().importKey(
    "raw",
    publicKeyBytes,
    ECDH_P256_ALGORITHM,
    false,
    []
  );
  const bits = await subtle().deriveBits({ name: "ECDH", public: publicKey }, privateKey, 256);
  const shared = new Uint8Array(bits);
  try {
    return SecureBuffer.fromBytes(shared);
  } finally {
    shared.fill(0);
  }
}
function split(derived, at) {
  try {
    return [derived.slice(0, at), derived.slice(at)];
  } finally {
    derived.fill(0);
  }
}
async function assertValidRemotePublicKey(publicKeyBytes) {
  try {
    await subtle().importKey(
      "raw",
      publicKeyBytes,
      ECDH_P256_ALGORITHM,
      false,
      []
    );
  } catch {
    throw new DisDecryptionError();
  }
}
async function rootKdf(rootKey, shared) {
  const derived = await shared.useAsync(
    (bytes) => deriveHkdfSha256Bits(bytes, {
      info: ROOT_KDF_INFO,
      salt: rootKey,
      lengthBits: RATCHET_KEY_LENGTH * 2 * 8
    })
  );
  const [nextRootKey, chainKey] = split(derived, RATCHET_KEY_LENGTH);
  return { rootKey: nextRootKey, chainKey };
}
async function chainKdf(chainKey) {
  const derived = await deriveHkdfSha256Bits(chainKey, {
    info: CHAIN_KDF_INFO,
    salt: ZERO_SALT,
    lengthBits: RATCHET_KEY_LENGTH * 2 * 8
  });
  const [nextChainKey, messageKey] = split(derived, RATCHET_KEY_LENGTH);
  return { chainKey: nextChainKey, messageKey };
}
async function expandMessageKey(messageKeyBytes) {
  const messageKey = SecureBuffer.fromBytes(messageKeyBytes);
  try {
    const derived = await messageKey.useAsync(
      (bytes) => deriveHkdfSha256Bits(bytes, {
        info: MESSAGE_KDF_INFO,
        salt: ZERO_SALT,
        lengthBits: MESSAGE_MATERIAL_BYTES * 8
      })
    );
    try {
      return SecureBuffer.fromBytes(derived);
    } finally {
      derived.fill(0);
    }
  } finally {
    messageKey.destroy();
  }
}
function canonicalHeaderBytes(header) {
  return utf8ToBytes(
    JSON.stringify({ v: header.v, dh: header.dh, pn: header.pn, n: header.n })
  );
}
function buildAad(header, associatedData) {
  return concatBytes(
    MESSAGE_KDF_INFO,
    AAD_SEPARATOR,
    associatedData ?? EMPTY_BYTES,
    AAD_SEPARATOR,
    canonicalHeaderBytes(header)
  );
}
async function sealMessage(messageKeyBytes, plaintext, header, associatedData) {
  const material = await expandMessageKey(messageKeyBytes);
  const aad = buildAad(header, associatedData);
  try {
    const ciphertext = await material.useAsync(
      (bytes) => aesGcmEncrypt(
        bytes.subarray(0, AES_KEY_BYTES),
        bytes.subarray(AES_KEY_BYTES, MESSAGE_MATERIAL_BYTES),
        plaintext,
        aad
      )
    );
    return bytesToBase64(ciphertext);
  } finally {
    material.destroy();
    aad.fill(0);
  }
}
async function openMessage(messageKeyBytes, message, associatedData) {
  let ciphertext;
  try {
    ciphertext = base64ToBytes(message.ciphertext);
  } catch {
    throw new DisDecryptionError();
  }
  const material = await expandMessageKey(messageKeyBytes);
  const aad = buildAad(message.header, associatedData);
  try {
    return await material.useAsync(
      (bytes) => aesGcmDecrypt(
        bytes.subarray(0, AES_KEY_BYTES),
        bytes.subarray(AES_KEY_BYTES, MESSAGE_MATERIAL_BYTES),
        ciphertext,
        aad
      )
    );
  } finally {
    material.destroy();
    aad.fill(0);
    ciphertext.fill(0);
  }
}
function isNonNegativeInt(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
function requireBytes(value, expectedLength, field) {
  if (!(value instanceof Uint8Array) || value.length !== expectedLength) {
    throw new DisInvalidArgumentError(`${field} must be ${expectedLength} bytes`);
  }
  return new Uint8Array(value);
}
function normaliseMaxSkippedKeys(value) {
  if (value === void 0) return DEFAULT_MAX_SKIPPED_KEYS;
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new DisInvalidArgumentError("maxSkippedKeys must be a positive integer");
  }
  return value;
}
function requireKeyPair(pair, field) {
  if (!pair || typeof pair !== "object") {
    throw new DisInvalidArgumentError(`${field} is required`);
  }
  if (pair.algorithm !== "ECDH-P-256") {
    throw new DisInvalidArgumentError(`${field}.algorithm must be ECDH-P-256`);
  }
  return {
    algorithm: "ECDH-P-256",
    publicKey: requireBytes(pair.publicKey, DH_PUBLIC_KEY_LENGTH, `${field}.publicKey`),
    privateKey: requireBytes(pair.privateKey, DH_PRIVATE_KEY_LENGTH, `${field}.privateKey`)
  };
}
function assertRatchetState(state) {
  if (!state || typeof state !== "object" || state.version !== 1) {
    throw new DisInvalidArgumentError("Invalid ratchet state");
  }
  if (!(state.rootKey instanceof Uint8Array) || state.rootKey.length !== RATCHET_KEY_LENGTH) {
    throw new DisInvalidArgumentError("Invalid ratchet state");
  }
  if (!state.dhSelf || state.dhSelf.algorithm !== "ECDH-P-256") {
    throw new DisInvalidArgumentError("Invalid ratchet state");
  }
}
function validateMessage(message) {
  if (!message || typeof message !== "object" || typeof message.ciphertext !== "string") {
    throw new DisDecryptionError();
  }
  const header = message.header;
  if (!header || typeof header !== "object" || header.v !== RATCHET_MESSAGE_V1_TAG) {
    throw new DisDecryptionError();
  }
  if (typeof header.dh !== "string" || !isNonNegativeInt(header.pn) || !isNonNegativeInt(header.n)) {
    throw new DisDecryptionError();
  }
  let dhBytes;
  try {
    dhBytes = base64ToBytes(header.dh);
  } catch {
    throw new DisDecryptionError();
  }
  if (dhBytes.length !== DH_PUBLIC_KEY_LENGTH) {
    throw new DisDecryptionError();
  }
  return { dh: header.dh, dhBytes, pn: header.pn, n: header.n };
}
async function initSenderState(input) {
  if (!input || typeof input !== "object") {
    throw new DisInvalidArgumentError("initSenderState input is required");
  }
  const sharedSecret = requireBytes(input.sharedSecret, RATCHET_KEY_LENGTH, "sharedSecret");
  const remotePublicKey = requireBytes(
    input.remotePublicKey,
    DH_PUBLIC_KEY_LENGTH,
    "remotePublicKey"
  );
  const maxSkippedKeys = normaliseMaxSkippedKeys(input.maxSkippedKeys);
  const dhSelf = await generateRatchetKeyPair();
  let shared = null;
  try {
    try {
      shared = await ecdhShared(dhSelf.privateKey, remotePublicKey);
    } catch {
      throw new DisInvalidArgumentError("remotePublicKey is not a valid P-256 public key");
    }
    const derived = await rootKdf(sharedSecret, shared);
    return {
      version: 1,
      dhSelf,
      dhRemote: remotePublicKey,
      rootKey: derived.rootKey,
      sendingChainKey: derived.chainKey,
      receivingChainKey: null,
      sendCount: 0,
      receiveCount: 0,
      previousSendCount: 0,
      skippedMessageKeys: [],
      maxSkippedKeys,
      associatedData: input.associatedData === void 0 ? null : new Uint8Array(input.associatedData)
    };
  } finally {
    shared?.destroy();
    sharedSecret.fill(0);
  }
}
async function initReceiverState(input) {
  if (!input || typeof input !== "object") {
    throw new DisInvalidArgumentError("initReceiverState input is required");
  }
  const rootKey = requireBytes(input.sharedSecret, RATCHET_KEY_LENGTH, "sharedSecret");
  const dhSelf = requireKeyPair(input.dhKeyPair, "dhKeyPair");
  const maxSkippedKeys = normaliseMaxSkippedKeys(input.maxSkippedKeys);
  return {
    version: 1,
    dhSelf,
    dhRemote: null,
    rootKey,
    sendingChainKey: null,
    receivingChainKey: null,
    sendCount: 0,
    receiveCount: 0,
    previousSendCount: 0,
    skippedMessageKeys: [],
    maxSkippedKeys,
    associatedData: input.associatedData === void 0 ? null : new Uint8Array(input.associatedData)
  };
}
function retainSkippedKey(working, entry) {
  while (working.skippedMessageKeys.length >= working.maxSkippedKeys && working.skippedMessageKeys.length > 0) {
    const evicted = working.skippedMessageKeys.shift();
    evicted?.messageKey.fill(0);
  }
  working.skippedMessageKeys.push(entry);
}
async function skipMessageKeys(working, chainDh, until) {
  if (until <= working.receiveCount) {
    return;
  }
  if (working.receivingChainKey === null) {
    throw new DisDecryptionError();
  }
  if (until - working.receiveCount > working.maxSkippedKeys) {
    throw new DisDecryptionError();
  }
  let chainKey = working.receivingChainKey;
  while (working.receiveCount < until) {
    const stepped = await chainKdf(chainKey);
    chainKey.fill(0);
    chainKey = stepped.chainKey;
    retainSkippedKey(working, {
      dh: chainDh,
      n: working.receiveCount,
      messageKey: stepped.messageKey
    });
    working.receiveCount += 1;
  }
  working.receivingChainKey = chainKey;
}
async function dhRatchet(working, remotePublicKey) {
  working.previousSendCount = working.sendCount;
  working.sendCount = 0;
  working.receiveCount = 0;
  working.dhRemote = new Uint8Array(remotePublicKey);
  const receivingShared = await ecdhShared(working.dhSelf.privateKey, remotePublicKey);
  try {
    const derived = await rootKdf(working.rootKey, receivingShared);
    working.rootKey.fill(0);
    working.rootKey = derived.rootKey;
    working.receivingChainKey?.fill(0);
    working.receivingChainKey = derived.chainKey;
  } finally {
    receivingShared.destroy();
  }
  const nextPair = await generateRatchetKeyPair();
  zeroBuffers(working.dhSelf.privateKey, working.dhSelf.publicKey);
  working.dhSelf = nextPair;
  const sendingShared = await ecdhShared(nextPair.privateKey, remotePublicKey);
  try {
    const derived = await rootKdf(working.rootKey, sendingShared);
    working.rootKey.fill(0);
    working.rootKey = derived.rootKey;
    working.sendingChainKey?.fill(0);
    working.sendingChainKey = derived.chainKey;
  } finally {
    sendingShared.destroy();
  }
}
async function encryptMessage(state, plaintext) {
  assertRatchetState(state);
  if (!(plaintext instanceof Uint8Array)) {
    throw new DisInvalidArgumentError("plaintext must be a Uint8Array");
  }
  if (state.sendingChainKey === null) {
    throw new DisInvalidArgumentError(
      "Ratchet has no sending chain yet; decrypt an inbound message first"
    );
  }
  const stepped = await chainKdf(state.sendingChainKey);
  try {
    const header = {
      v: RATCHET_MESSAGE_V1_TAG,
      dh: bytesToBase64(state.dhSelf.publicKey),
      pn: state.previousSendCount,
      n: state.sendCount
    };
    const ciphertext = await sealMessage(
      stepped.messageKey,
      plaintext,
      header,
      state.associatedData
    );
    const nextState = cloneState(state);
    nextState.sendingChainKey?.fill(0);
    nextState.sendingChainKey = stepped.chainKey;
    nextState.sendCount = state.sendCount + 1;
    return { nextState, message: { header, ciphertext } };
  } catch (error) {
    stepped.chainKey.fill(0);
    throw error;
  } finally {
    stepped.messageKey.fill(0);
  }
}
async function decryptMessage(state, message) {
  assertRatchetState(state);
  const header = validateMessage(message);
  const skippedIndex = state.skippedMessageKeys.findIndex(
    (entry) => entry.n === header.n && entry.dh === header.dh
  );
  if (skippedIndex >= 0) {
    const entry = state.skippedMessageKeys[skippedIndex];
    const plaintext = await openMessage(entry.messageKey, message, state.associatedData);
    const nextState = cloneState(state);
    const [consumed] = nextState.skippedMessageKeys.splice(skippedIndex, 1);
    consumed?.messageKey.fill(0);
    return { nextState, plaintext };
  }
  const currentRemote = state.dhRemote === null ? null : bytesToBase64(state.dhRemote);
  if (currentRemote === header.dh && header.n < state.receiveCount) {
    throw new DisDecryptionError();
  }
  const working = cloneState(state);
  let committed = false;
  try {
    if (currentRemote !== header.dh) {
      await assertValidRemotePublicKey(header.dhBytes);
      if (currentRemote !== null) {
        await skipMessageKeys(working, currentRemote, header.pn);
      } else if (header.pn !== 0) {
        throw new DisDecryptionError();
      }
      await dhRatchet(working, header.dhBytes);
    }
    await skipMessageKeys(working, header.dh, header.n);
    const receivingChainKey = working.receivingChainKey;
    if (receivingChainKey === null) {
      throw new DisDecryptionError();
    }
    const stepped = await chainKdf(receivingChainKey);
    try {
      const plaintext = await openMessage(
        stepped.messageKey,
        message,
        working.associatedData
      );
      receivingChainKey.fill(0);
      working.receivingChainKey = stepped.chainKey;
      working.receiveCount += 1;
      committed = true;
      return { nextState: working, plaintext };
    } catch (error) {
      stepped.chainKey.fill(0);
      throw error;
    } finally {
      stepped.messageKey.fill(0);
    }
  } finally {
    if (!committed) {
      destroyRatchetState(working);
    }
  }
}
function destroyRatchetState(state) {
  if (!state || typeof state !== "object") {
    return;
  }
  zeroBuffers(
    state.rootKey,
    state.sendingChainKey,
    state.receivingChainKey,
    state.dhSelf?.privateKey,
    state.dhSelf?.publicKey,
    state.dhRemote,
    state.associatedData
  );
  for (const entry of state.skippedMessageKeys ?? []) {
    entry.messageKey?.fill(0);
  }
}
function encodeOptional(bytes) {
  return bytes === null ? null : bytesToBase64(bytes);
}
function serializeRatchetState(state) {
  assertRatchetState(state);
  const body = {
    version: 1,
    dhSelf: {
      algorithm: state.dhSelf.algorithm,
      publicKey: bytesToBase64(state.dhSelf.publicKey),
      privateKey: bytesToBase64(state.dhSelf.privateKey)
    },
    dhRemote: encodeOptional(state.dhRemote),
    rootKey: bytesToBase64(state.rootKey),
    sendingChainKey: encodeOptional(state.sendingChainKey),
    receivingChainKey: encodeOptional(state.receivingChainKey),
    sendCount: state.sendCount,
    receiveCount: state.receiveCount,
    previousSendCount: state.previousSendCount,
    skippedMessageKeys: state.skippedMessageKeys.map((entry) => ({
      dh: entry.dh,
      n: entry.n,
      messageKey: bytesToBase64(entry.messageKey)
    })),
    maxSkippedKeys: state.maxSkippedKeys,
    associatedData: encodeOptional(state.associatedData)
  };
  return formatEnvelope(STATE_ENVELOPE_SPEC, JSON.stringify(body));
}
function decodeBase64(value, field) {
  if (typeof value !== "string") {
    throw new DisInvalidArgumentError(`${field} must be a base64 string`);
  }
  try {
    return base64ToBytes(value);
  } catch {
    throw new DisInvalidArgumentError(`${field} is not valid base64`);
  }
}
function decodeFixed(value, expectedLength, field) {
  const bytes = decodeBase64(value, field);
  if (bytes.length !== expectedLength) {
    bytes.fill(0);
    throw new DisInvalidArgumentError(`${field} must decode to ${expectedLength} bytes`);
  }
  return bytes;
}
function decodeOptionalFixed(value, expectedLength, field) {
  return value === null || value === void 0 ? null : decodeFixed(value, expectedLength, field);
}
function decodeCount(value, field) {
  if (!isNonNegativeInt(value)) {
    throw new DisInvalidArgumentError(`${field} must be a non-negative integer`);
  }
  return value;
}
function deserializeRatchetState(json) {
  if (typeof json !== "string" || json.length === 0) {
    throw new DisInvalidArgumentError("Ratchet state must be a non-empty string");
  }
  const envelope = parseEnvelope(STATE_ENVELOPE_SPEC, json);
  if (envelope.version !== 1) {
    throw new DisInvalidArgumentError("Unrecognised ratchet state envelope");
  }
  let parsed;
  try {
    parsed = JSON.parse(envelope.payload);
  } catch {
    throw new DisInvalidArgumentError("Malformed ratchet state JSON");
  }
  if (!parsed || typeof parsed !== "object") {
    throw new DisInvalidArgumentError("Malformed ratchet state JSON");
  }
  const body = parsed;
  if (body.version !== 1) {
    throw new DisInvalidArgumentError("Unsupported ratchet state version");
  }
  const dhSelf = body.dhSelf;
  if (!dhSelf || typeof dhSelf !== "object" || dhSelf.algorithm !== "ECDH-P-256") {
    throw new DisInvalidArgumentError("Unsupported ratchet DH algorithm");
  }
  if (!Array.isArray(body.skippedMessageKeys)) {
    throw new DisInvalidArgumentError("skippedMessageKeys must be an array");
  }
  const maxSkippedKeys = normaliseMaxSkippedKeys(body.maxSkippedKeys);
  if (body.skippedMessageKeys.length > maxSkippedKeys) {
    throw new DisInvalidArgumentError("skippedMessageKeys exceeds maxSkippedKeys");
  }
  const skippedMessageKeys = body.skippedMessageKeys.map((entry, index) => {
    if (!entry || typeof entry !== "object" || typeof entry.dh !== "string") {
      throw new DisInvalidArgumentError(`skippedMessageKeys[${index}] is malformed`);
    }
    return {
      dh: entry.dh,
      n: decodeCount(entry.n, `skippedMessageKeys[${index}].n`),
      messageKey: decodeFixed(
        entry.messageKey,
        RATCHET_KEY_LENGTH,
        `skippedMessageKeys[${index}].messageKey`
      )
    };
  });
  return {
    version: 1,
    dhSelf: {
      algorithm: "ECDH-P-256",
      publicKey: decodeFixed(dhSelf.publicKey, DH_PUBLIC_KEY_LENGTH, "dhSelf.publicKey"),
      privateKey: decodeFixed(dhSelf.privateKey, DH_PRIVATE_KEY_LENGTH, "dhSelf.privateKey")
    },
    dhRemote: decodeOptionalFixed(body.dhRemote, DH_PUBLIC_KEY_LENGTH, "dhRemote"),
    rootKey: decodeFixed(body.rootKey, RATCHET_KEY_LENGTH, "rootKey"),
    sendingChainKey: decodeOptionalFixed(
      body.sendingChainKey,
      RATCHET_KEY_LENGTH,
      "sendingChainKey"
    ),
    receivingChainKey: decodeOptionalFixed(
      body.receivingChainKey,
      RATCHET_KEY_LENGTH,
      "receivingChainKey"
    ),
    sendCount: decodeCount(body.sendCount, "sendCount"),
    receiveCount: decodeCount(body.receiveCount, "receiveCount"),
    previousSendCount: decodeCount(body.previousSendCount, "previousSendCount"),
    skippedMessageKeys,
    maxSkippedKeys,
    associatedData: body.associatedData === null || body.associatedData === void 0 ? null : decodeBase64(body.associatedData, "associatedData")
  };
}
function serializeRatchetMessage(message) {
  if (!message || typeof message !== "object" || typeof message.ciphertext !== "string") {
    throw new DisInvalidArgumentError("Invalid ratchet message");
  }
  const header = message.header;
  if (!header || header.v !== RATCHET_MESSAGE_V1_TAG || typeof header.dh !== "string" || !isNonNegativeInt(header.pn) || !isNonNegativeInt(header.n)) {
    throw new DisInvalidArgumentError("Invalid ratchet message header");
  }
  return formatEnvelope(
    MESSAGE_ENVELOPE_SPEC,
    JSON.stringify({
      header: { v: header.v, dh: header.dh, pn: header.pn, n: header.n },
      ciphertext: message.ciphertext
    })
  );
}
function deserializeRatchetMessage(wire) {
  if (typeof wire !== "string" || wire.length === 0) {
    throw new DisInvalidArgumentError("Ratchet message must be a non-empty string");
  }
  const envelope = parseEnvelope(MESSAGE_ENVELOPE_SPEC, wire);
  if (envelope.version !== 1) {
    throw new DisInvalidArgumentError("Unrecognised ratchet message envelope");
  }
  let parsed;
  try {
    parsed = JSON.parse(envelope.payload);
  } catch {
    throw new DisInvalidArgumentError("Malformed ratchet message JSON");
  }
  if (!parsed || typeof parsed !== "object") {
    throw new DisInvalidArgumentError("Malformed ratchet message JSON");
  }
  const body = parsed;
  const header = body.header;
  if (!header || typeof header !== "object" || header.v !== RATCHET_MESSAGE_V1_TAG || typeof header.dh !== "string" || !isNonNegativeInt(header.pn) || !isNonNegativeInt(header.n) || typeof body.ciphertext !== "string") {
    throw new DisInvalidArgumentError("Malformed ratchet message");
  }
  return {
    header: { v: RATCHET_MESSAGE_V1_TAG, dh: header.dh, pn: header.pn, n: header.n },
    ciphertext: body.ciphertext
  };
}

export { DEFAULT_MAX_SKIPPED_KEYS, RATCHET_MESSAGE_V1_PREFIX, RATCHET_STATE_V1_PREFIX, decryptMessage, deserializeRatchetMessage, deserializeRatchetState, destroyRatchetState, encryptMessage, generateRatchetKeyPair, initReceiverState, initSenderState, serializeRatchetMessage, serializeRatchetState };
//# sourceMappingURL=chunk-L3SYMJ2V.js.map
//# sourceMappingURL=chunk-L3SYMJ2V.js.map