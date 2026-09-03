import { VersionedCipherEnvelopeSpec } from '../format-versioning/index.js';

/**
 * dis-vault-encryption — encryption of structured vault entries.
 *
 * A vault entry is an arbitrary JSON-serialisable record. It is sealed with
 * AES-256-GCM and wrapped in the versioned `sv-vault-v1:` envelope, byte
 * compatible with Singra Vault. The entry id is passed as AEAD associated data
 * so ciphertext is cryptographically bound to its row (defeats swap attacks).
 *
 * Legacy (pre-versioning, no-AAD) payloads fail closed on the runtime read
 * path and are only readable through the explicit migration helper.
 */

declare const VAULT_ITEM_ENVELOPE_V1_PREFIX = "sv-vault-v1:";
declare const VAULT_ITEM_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec;
type VaultEntryData = Record<string, unknown>;
/** Seals a vault entry, binding it to `entryId` via AEAD associated data. */
declare function encryptVaultEntry(data: VaultEntryData, key: CryptoKey, entryId: string): Promise<string>;
/**
 * Opens a vault entry. Versioned payloads are read with `entryId` as AAD.
 * Legacy no-AAD payloads throw {@link DisLegacyPayloadError} on the runtime
 * path; use {@link decryptVaultEntryForMigration} to read and rewrite them.
 */
declare function decryptVaultEntry(encryptedData: string, key: CryptoKey, entryId: string): Promise<VaultEntryData>;
interface VaultEntryMigrationResult {
    readonly data: VaultEntryData;
    readonly legacyEnvelopeUsed: boolean;
    readonly legacyNoAadFallbackUsed: boolean;
}
/**
 * Decrypts an entry on an explicit migration path, permitting the no-AAD
 * fallback for the oldest payloads so they can be rewritten as versioned,
 * AAD-bound items. Never use on the normal runtime read path.
 */
declare function decryptVaultEntryForMigration(encryptedData: string, key: CryptoKey, entryId: string): Promise<VaultEntryMigrationResult>;
/** True if `encryptedData` is a current versioned vault-item envelope. */
declare function isCurrentVaultEntryEnvelope(encryptedData: string): boolean;

export { VAULT_ITEM_ENVELOPE_SPEC, VAULT_ITEM_ENVELOPE_V1_PREFIX, type VaultEntryData, type VaultEntryMigrationResult, decryptVaultEntry, decryptVaultEntryForMigration, encryptVaultEntry, isCurrentVaultEntryEnvelope };
