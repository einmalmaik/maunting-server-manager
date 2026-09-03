import { encryptString, decryptString } from './chunk-U65A4HIY.js';
import { formatEnvelope, parseEnvelope, isCurrentEnvelope } from './chunk-SCHZI6YY.js';
import { DisInvalidArgumentError, DisLegacyPayloadError } from './chunk-MJO7IJZC.js';

// src/vault-encryption/index.ts
var VAULT_ITEM_ENVELOPE_V1_PREFIX = "sv-vault-v1:";
var VAULT_ITEM_ENVELOPE_FAMILY_PREFIX = "sv-vault-";
var VAULT_ITEM_ENVELOPE_SPEC = {
  currentPrefix: VAULT_ITEM_ENVELOPE_V1_PREFIX,
  familyPrefix: VAULT_ITEM_ENVELOPE_FAMILY_PREFIX,
  subject: "vault item"
};
async function encryptVaultEntry(data, key, entryId) {
  if (!entryId) {
    throw new DisInvalidArgumentError("entryId is required to bind vault entry ciphertext");
  }
  const json = JSON.stringify(data);
  return formatEnvelope(VAULT_ITEM_ENVELOPE_SPEC, await encryptString(json, key, entryId));
}
async function decryptVaultEntry(encryptedData, key, entryId) {
  const envelope = parseEnvelope(VAULT_ITEM_ENVELOPE_SPEC, encryptedData);
  if (envelope.version === 1) {
    return JSON.parse(await decryptString(envelope.payload, key, entryId));
  }
  if (entryId) {
    try {
      return JSON.parse(
        await decryptString(envelope.payload, key, entryId)
      );
    } catch {
      throw new DisLegacyPayloadError("Legacy vault item without AAD requires migration.");
    }
  }
  throw new DisLegacyPayloadError("Legacy vault item without AAD requires migration.");
}
async function decryptVaultEntryForMigration(encryptedData, key, entryId) {
  const envelope = parseEnvelope(VAULT_ITEM_ENVELOPE_SPEC, encryptedData);
  if (envelope.version === 1) {
    return {
      data: JSON.parse(await decryptString(envelope.payload, key, entryId)),
      legacyEnvelopeUsed: false,
      legacyNoAadFallbackUsed: false
    };
  }
  if (entryId) {
    try {
      return {
        data: JSON.parse(
          await decryptString(envelope.payload, key, entryId)
        ),
        legacyEnvelopeUsed: true,
        legacyNoAadFallbackUsed: false
      };
    } catch {
    }
  }
  const data = JSON.parse(await decryptString(envelope.payload, key));
  return { data, legacyEnvelopeUsed: true, legacyNoAadFallbackUsed: true };
}
function isCurrentVaultEntryEnvelope(encryptedData) {
  return isCurrentEnvelope(VAULT_ITEM_ENVELOPE_SPEC, encryptedData);
}

export { VAULT_ITEM_ENVELOPE_SPEC, VAULT_ITEM_ENVELOPE_V1_PREFIX, decryptVaultEntry, decryptVaultEntryForMigration, encryptVaultEntry, isCurrentVaultEntryEnvelope };
//# sourceMappingURL=chunk-JVFP2GAO.js.map
//# sourceMappingURL=chunk-JVFP2GAO.js.map