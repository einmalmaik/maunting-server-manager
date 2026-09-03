export { VAULT_ITEM_ENVELOPE_V1_PREFIX, VaultEntryData, VaultEntryMigrationResult, decryptVaultEntry, decryptVaultEntryForMigration, encryptVaultEntry, isCurrentVaultEntryEnvelope } from './vault-encryption/index.js';
export { AttachmentContext, DEFAULT_CHUNK_SIZE, DecryptAttachmentInput, EncryptAttachmentInput, FILE_MANIFEST_V1_PREFIX, FileChunkManifest, FileManifestV1, decryptAttachment, decryptChunk, encryptAttachment, encryptChunk, generateFileKeyBytes, importFileKey } from './file-encryption/index.js';
export { Argon2idRawParams, CURRENT_KDF_VERSION, DEFAULT_KDF_PARAMS, DeriveRawKeyOptions, KdfParams, argon2idRaw, deriveHkdfAesGcmKey, deriveHkdfSha256Bits, deriveAesGcmKey as deriveMasterKey, deriveRawKey, generateSalt, importAesGcmKey } from './kdf/index.js';
export { DEFAULT_KEY_WRAP_SCHEME, KeyWrapScheme, UserKeyBundle, createWrappedUserKey, generateContentKeyBytes, rotateWrappedKey as rotateEncryptionKeys, unwrapUserKey, unwrapUserKeyBytes } from './key-management/index.js';
export { HYBRID_VERSION, HybridKeyPair, PQKeyPair, SECURITY_STANDARD_VERSION, SharedKeyWrapAadInput, buildSharedKeyWrapAad, generateHybridKeyPair, generatePQKeyPair, hybridDecrypt, hybridEncrypt, hybridUnwrapKey, hybridWrapKey, isCurrentStandardEncrypted, isHybridEncrypted, migrateToHybrid } from './post-quantum/index.js';
export { constantTimeEqual, hmacSha256, hmacSha256WithKey, importHmacSha256Key, sha1Hex, sha256Base64, sha256Base64Url, sha256Bytes, sha256Hex, sha256JsonBase64, verifyPayloadIntegrity } from './integrity/index.js';
export { ECDSA_P256_SIGNATURE_LENGTH, EcdsaP256KeyPair, generateEcdsaP256KeyPair, importEcdsaP256PublicKeySpki, signEcdsaP256, verifyEcdsaP256 } from './signing/index.js';
export { TotpParams, buildTotpUri, generateTotpSecret, verifyTotpCode } from './totp/index.js';
export { Migration, MigrationContext, MigrationRegistry, VersionDetector } from './migrations/index.js';
export { aesGcmDecrypt, aesGcmEncrypt, decryptBytes, decryptString, encryptBytes, encryptString, generateAesGcmKey, importAesGcmRawKey } from './aead/index.js';
export { SecureBuffer, withSecureBuffer, zeroBuffers } from './secure-memory/index.js';
export { fillRandom, randomBytes, randomInt, randomUuid } from './random/index.js';
export { VersionedCipherEnvelope, VersionedCipherEnvelopeSpec, formatEnvelope, isCurrentEnvelope, parseEnvelope } from './format-versioning/index.js';
export { C as CryptoProvider, D as DisDecryptionError, a as DisError, b as DisErrorCode, c as DisIntegrityError, d as DisInvalidArgumentError, e as DisLegacyPayloadError, f as DisUnsupportedFormatVersionError, g as getCryptoProvider, s as setCryptoProvider } from './errors-C79jA9vX.js';

/**
 * DIS — Defensive Integration Shield
 * Powered by DIS — Defensive Integration Shield.
 *
 * Stable public SDK facade. Applications should depend on this surface (or the
 * individual `@dis/shield/<module>` entry points) and never call low-level
 * WebCrypto directly. Internal cryptographic changes are designed not to break
 * these signatures.
 */
declare const DIS_BRANDING: "Powered by DIS \u2014 Defensive Integration Shield";

export { DIS_BRANDING };
