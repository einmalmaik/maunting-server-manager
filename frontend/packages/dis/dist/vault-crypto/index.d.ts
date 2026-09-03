export { b as base64ToBytes, c as bytesToBase64 } from '../encoding-B-cb7Duu.js';
import { KdfParams as KdfParams$1 } from '../kdf/index.js';
import { UserKeyBundle as UserKeyBundle$1 } from '../key-management/index.js';
import { SecureBuffer } from '../secure-memory/index.js';

/**
 * dis-vault-crypto — the Singra Vault *application crypto profile*.
 *
 * This module is the stable, named API that Singra Vault (and, transitively,
 * Singra Premium) consume. It does NOT re-implement any primitive: every
 * operation is composed from the audited DIS modules (`kdf`, `aead`,
 * `vault-encryption`, `key-management`, `asymmetric`, `post-quantum`). What it
 * adds is the *application-specific composition and versioned envelope formats*
 * that Singra has always used:
 *
 *   - device-key strengthened Argon2id derivation (HKDF info `SINGRA_DEVICE_KEY_V1`)
 *   - the `sv-vault-v1:` vault-item envelope (entry-id AAD)
 *   - the two-tier UserKey model (`usk-wrap-v2:`) and private-key wrapping (`usk-v1:`)
 *   - sharing / emergency-access key material (`pq-v2:` hybrid keypair envelope)
 *   - KDF auto-upgrade, verification hashes, and re-encryption helpers
 *
 * Keeping these names and formats here means applications need ZERO crypto
 * code of their own — they import this profile and nothing else. Every byte
 * format is covered by golden-vector tests proving compatibility with the
 * pre-extraction Singra implementation.
 */

/** The latest KDF version. Newly set-up accounts use this version. */
declare const CURRENT_KDF_VERSION = 2;
/** Argon2id parameter set for a given KDF version. */
type KdfParams = KdfParams$1;
/**
 * KDF parameter sets indexed by version number. Byte-compatible with Singra.
 * Exposed as a plain record for call sites that look up params by version.
 */
declare const KDF_PARAMS: Record<number, KdfParams>;
/** Versioned envelope prefix for vault item payloads. */
declare const VAULT_ITEM_ENVELOPE_V1_PREFIX = "sv-vault-v1:";
type UserKeyBundle = UserKeyBundle$1;
/** Sensitive vault item data that gets encrypted. */
interface VaultItemData {
    title?: string;
    websiteUrl?: string;
    itemType?: 'password' | 'note' | 'totp' | 'card';
    isFavorite?: boolean;
    categoryId?: string | null;
    username?: string;
    password?: string;
    notes?: string;
    totpSecret?: string;
    totpIssuer?: string;
    totpLabel?: string;
    totpAlgorithm?: 'SHA1' | 'SHA256' | 'SHA512';
    totpDigits?: 6 | 8;
    totpPeriod?: number;
    customFields?: Record<string, string>;
    /** Internal marker for duress/decoy items (never exposed to UI). */
    _duress?: boolean;
}
/** Generates a cryptographically secure random salt (base64, 128-bit). */
declare function generateSalt(): string;
/**
 * Derives raw AES-256 key bytes from a master password using Argon2id.
 * When a deviceKey is provided, the result is strengthened via HKDF-Expand
 * with the device key as salt. Caller owns/must wipe the returned buffer.
 */
declare function deriveRawKey(masterPassword: string, saltBase64: string, kdfVersion?: number, deviceKey?: Uint8Array): Promise<Uint8Array>;
/**
 * Derives raw key bytes wrapped in a SecureBuffer for safer handling.
 * The SecureBuffer auto-zeros on destroy. Caller MUST call `.destroy()`.
 */
declare function deriveRawKeySecure(masterPassword: string, saltBase64: string, kdfVersion?: number, deviceKey?: Uint8Array): Promise<SecureBuffer>;
/** Derives an AES-256-GCM CryptoKey from a master password. */
declare function deriveKey(masterPassword: string, saltBase64: string, kdfVersion?: number, deviceKey?: Uint8Array): Promise<CryptoKey>;
/** Imports raw 256-bit key bytes into a non-extractable AES-GCM CryptoKey. */
declare function importMasterKey(keyBytes: Uint8Array | BufferSource): Promise<CryptoKey>;
/** Encrypts a UTF-8 string with AES-256-GCM. Optional AAD binds context. */
declare function encrypt(plaintext: string, key: CryptoKey, aad?: string): Promise<string>;
/** Encrypts binary data with AES-256-GCM. Caller owns/wipes `plaintextBytes`. */
declare function encryptBytes(plaintextBytes: Uint8Array, key: CryptoKey, aad?: string): Promise<string>;
/** Decrypts AES-256-GCM data to a UTF-8 string. */
declare function decrypt(encryptedBase64: string, key: CryptoKey, aad?: string): Promise<string>;
/** Decrypts AES-256-GCM data to plaintext bytes (secret — caller must wipe). */
declare function decryptBytes(encryptedBase64: string, key: CryptoKey, aad?: string): Promise<Uint8Array>;
/** Encrypts a vault item, binding the ciphertext to `entryId` via AAD. */
declare function encryptVaultItem(data: VaultItemData, key: CryptoKey, entryId: string): Promise<string>;
/**
 * Decrypts a vault item. Versioned payloads are read with `entryId` as AAD and
 * fail closed for the oldest no-AAD payloads unless the explicit migration
 * fallback is requested.
 */
declare function decryptVaultItem(encryptedData: string, key: CryptoKey, entryId: string, options?: {
    allowLegacyNoAadFallback?: boolean;
}): Promise<VaultItemData>;
interface VaultItemMigrationDecryptResult {
    data: VaultItemData;
    legacyEnvelopeUsed: boolean;
    legacyNoAadFallbackUsed: boolean;
}
/**
 * Decrypts an item only for an explicit migration path. Runtime reads must use
 * decryptVaultItem(), which fails closed for legacy no-AAD payloads.
 */
declare function decryptVaultItemForMigration(encryptedData: string, key: CryptoKey, entryId: string): Promise<VaultItemMigrationDecryptResult>;
/**
 * True if `encryptedData` is a current versioned vault-item envelope.
 *
 * Fails closed (throws) on an unknown in-family version (`sv-vault-v<n>:`) so a
 * future format can never be silently treated as legacy by migration code —
 * matching the Singra contract. Callers that need a non-throwing predicate must
 * wrap this explicitly.
 */
declare function isCurrentVaultItemEnvelope(encryptedData: string): boolean;
/** Creates a password verification hash (v3: encrypts a known constant). */
declare function createVerificationHash(key: CryptoKey): Promise<string>;
/** Verifies that `key` can decrypt the stored verification hash. */
declare function verifyKey(verificationHash: string, key: CryptoKey): Promise<boolean>;
interface KdfUpgradeResult {
    upgraded: boolean;
    newKey?: CryptoKey;
    oldKey?: CryptoKey;
    newVerifier?: string;
    activeVersion: number;
    newEncryptedUserKey?: string;
}
/**
 * Attempts to upgrade KDF parameters to the latest version after unlock.
 * USK path only re-wraps the 32-byte UserKey (no vault re-encryption). Legacy
 * path returns old+new keys so the caller can re-encrypt vault data.
 */
declare function attemptKdfUpgrade(masterPassword: string, saltBase64: string, currentVersion: number, deviceKey?: Uint8Array, encryptedUserKey?: string, existingKdfOutputBytes?: Uint8Array): Promise<KdfUpgradeResult>;
/** Re-encrypts a single encrypted string from oldKey to newKey. */
declare function reEncryptString(encryptedBase64: string, oldKey: CryptoKey, newKey: CryptoKey, aad?: string): Promise<string>;
interface ReEncryptionResult {
    itemsReEncrypted: number;
    categoriesReEncrypted: number;
    itemUpdates: Array<{
        id: string;
        encrypted_data: string;
    }>;
    categoryUpdates: Array<{
        id: string;
        name: string;
        icon: string | null;
        color: string | null;
    }>;
    legacyItemsFound: number;
}
/**
 * Re-encrypts all vault items and encrypted category fields from an old key to
 * a new key (required during KDF upgrades). Pure: no DB side effects.
 */
declare function reEncryptVault(items: Array<{
    id: string;
    encrypted_data: string;
}>, categories: Array<{
    id: string;
    name: string;
    icon: string | null;
    color: string | null;
}>, oldKey: CryptoKey, newKey: CryptoKey): Promise<ReEncryptionResult>;
/**
 * Clears sensitive references from a VaultItemData object. NOTE: JS strings are
 * immutable; this only drops references so the GC can reclaim them sooner.
 */
declare function clearReferences(data: VaultItemData): void;
/** @deprecated Use clearReferences. secureClear implies wiping JS cannot do. */
declare const secureClear: typeof clearReferences;
declare function generateRSAKeyPair(): Promise<CryptoKeyPair>;
declare function exportPublicKey(key: CryptoKey): Promise<JsonWebKey>;
declare function importPublicKey(jwk: JsonWebKey): Promise<CryptoKey>;
declare function importPrivateKey(jwk: JsonWebKey): Promise<CryptoKey>;
declare function exportPrivateKey(key: CryptoKey): Promise<JsonWebKey>;
declare function encryptRSA(plaintext: string, publicKey: CryptoKey): Promise<string>;
declare function decryptRSA(ciphertextBase64: string, privateKey: CryptoKey): Promise<string>;
/**
 * Generates a user's asymmetric key material for shared collections.
 * v1: RSA-only wrapping. v2: hybrid PQ+RSA (`pq-v2:` envelope).
 */
declare function generateUserKeyPair(masterPassword: string, version?: 1 | 2): Promise<{
    publicKey: string;
    encryptedPrivateKey: string;
    pqPublicKey?: string;
}>;
/** Migrates RSA-only wrapping key material to hybrid PQ+RSA key material. */
declare function migrateToHybridKeyPair(encryptedPrivateKey: string, masterPassword: string): Promise<{
    publicKey: string;
    encryptedPrivateKey: string;
    pqPublicKey: string;
} | null>;
/** Generates a random shared encryption key for a collection (AES-256 JWK). */
declare function generateSharedKey(): Promise<string>;
/** Encrypts vault item data with a shared key. Optional AAD binds context. */
declare function encryptWithSharedKey(data: VaultItemData, sharedKey: string, aad?: string): Promise<string>;
interface SharedKeyDecryptOptions {
    /** Allows reading pre-AAD shared ciphertexts during explicit migration only. */
    allowLegacyNoAadFallback?: boolean;
}
/** Decrypts vault item data with a shared key. Fails closed by default. */
declare function decryptWithSharedKey(encryptedData: string, sharedKey: string, aad?: string, options?: SharedKeyDecryptOptions): Promise<VaultItemData>;
/** Creates a new random UserKey wrapped under a KEK from the KDF output. */
declare function createEncryptedUserKey(kdfOutputBytes: Uint8Array): Promise<UserKeyBundle>;
/** Derives a deterministic UserKey from the KDF output and wraps it (migration). */
declare function migrateToUserKey(kdfOutputBytes: Uint8Array): Promise<UserKeyBundle>;
/** Decrypts the stored encryptedUserKey to obtain the UserKey CryptoKey. */
declare function unwrapUserKey(encryptedUserKey: string, kdfOutputBytes: Uint8Array): Promise<CryptoKey>;
/** Decrypts the stored encryptedUserKey and returns the raw UserKey bytes. */
declare function unwrapUserKeyBytes(encryptedUserKey: string, kdfOutputBytes: Uint8Array): Promise<Uint8Array>;
/** Re-wraps an existing UserKey under a new KDF output. UserKey unchanged. */
declare function rewrapUserKey(encryptedUserKey: string, oldKdfOutputBytes: Uint8Array, newKdfOutputBytes: Uint8Array): Promise<string>;
/** Encrypts a private key (RSA JWK / PQ base64) with the UserKey (`usk-v1:`). */
declare function wrapPrivateKeyWithUserKey(privateKeyMaterial: string, userKey: CryptoKey): Promise<string>;
/** Decrypts a private key wrapped with wrapPrivateKeyWithUserKey. */
declare function unwrapPrivateKeyWithUserKey(wrappedKey: string, userKey: CryptoKey): Promise<string>;
/**
 * Decrypts a legacy private key encrypted with its own KDF derivation.
 * Handles `kdfVersion:salt:enc`, `salt:enc`, and `pq-v2:kdfVersion:salt:encRsa:encPq`.
 */
declare function decryptPrivateKeyLegacy(encryptedPrivateKey: string, masterPassword: string, extractPqPart?: boolean): Promise<string>;
/** Decrypts a stored RSA private key, dispatching on format sentinel. */
declare function getDecryptedRsaPrivateKey(encryptedPrivateKey: string, userKey: CryptoKey | null, masterPassword: string): Promise<string>;
/** Decrypts a stored PQ (ML-KEM-768) private key, dispatching on format sentinel. */
declare function getDecryptedPqPrivateKey(encryptedPqPrivateKey: string, userKey: CryptoKey | null, masterPassword: string): Promise<string>;

export { CURRENT_KDF_VERSION, KDF_PARAMS, type KdfParams, type KdfUpgradeResult, type ReEncryptionResult, type SharedKeyDecryptOptions, type UserKeyBundle, VAULT_ITEM_ENVELOPE_V1_PREFIX, type VaultItemData, type VaultItemMigrationDecryptResult, attemptKdfUpgrade, clearReferences, createEncryptedUserKey, createVerificationHash, decrypt, decryptBytes, decryptPrivateKeyLegacy, decryptRSA, decryptVaultItem, decryptVaultItemForMigration, decryptWithSharedKey, deriveKey, deriveRawKey, deriveRawKeySecure, encrypt, encryptBytes, encryptRSA, encryptVaultItem, encryptWithSharedKey, exportPrivateKey, exportPublicKey, generateRSAKeyPair, generateSalt, generateSharedKey, generateUserKeyPair, getDecryptedPqPrivateKey, getDecryptedRsaPrivateKey, importMasterKey, importPrivateKey, importPublicKey, isCurrentVaultItemEnvelope, migrateToHybridKeyPair, migrateToUserKey, reEncryptString, reEncryptVault, rewrapUserKey, secureClear, unwrapPrivateKeyWithUserKey, unwrapUserKey, unwrapUserKeyBytes, verifyKey, wrapPrivateKeyWithUserKey };
