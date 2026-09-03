export { importAesGcmKey } from '../kdf/index.js';

/**
 * dis-key-management — key hierarchy, wrapping, and rotation.
 *
 * Implements the two-tier key model used by Singra Vault:
 *   - A high-entropy random *content key* (the "UserKey") encrypts vault data.
 *   - The content key is wrapped under a *key-encryption key* (KEK) that is
 *     derived from the KDF output via HKDF-Expand (domain-separated `info`).
 *
 * Because the content key is independent of the password, changing the master
 * password only re-wraps the content key — no vault data is re-encrypted. This
 * is what makes {@link rotateWrappedKey} cheap and safe.
 *
 * Wrapped keys carry a stable, versioned prefix. The default scheme is byte
 * compatible with Singra's `usk-wrap-v2:` envelope.
 */

/** Describes how a content key is wrapped: envelope prefix + HKDF domain. */
interface KeyWrapScheme {
    /** Stable envelope prefix written for new wraps, e.g. `usk-wrap-v2:`. */
    readonly prefix: string;
    /** HKDF-Expand `info` for deriving the KEK from KDF output. */
    readonly hkdfInfo: string;
}
/**
 * Default wrap scheme. Byte-compatible with Singra Vault so existing
 * `encrypted_user_key` values decrypt unchanged. The `info` label is part of
 * the format contract and must not be renamed without a new scheme version.
 */
declare const DEFAULT_KEY_WRAP_SCHEME: KeyWrapScheme;

interface UserKeyBundle {
    /** Wrapped content key: `<prefix><base64(IV||CT||tag)>`. */
    readonly encryptedUserKey: string;
    /** Non-extractable AES-GCM content key, ready for vault operations. */
    readonly userKey: CryptoKey;
}
/** Generates fresh random content-key bytes (caller must wipe). */
declare function generateContentKeyBytes(): Uint8Array;
/**
 * Creates a new random content key and wraps it under the KEK derived from
 * `kdfOutputBytes`. For new accounts. The caller still owns `kdfOutputBytes`.
 */
declare function createWrappedUserKey(kdfOutputBytes: Uint8Array, scheme?: KeyWrapScheme): Promise<UserKeyBundle>;
/** Unwraps a wrapped content key to raw bytes (caller must wipe). */
declare function unwrapUserKeyBytes(encryptedUserKey: string, kdfOutputBytes: Uint8Array, scheme?: KeyWrapScheme): Promise<Uint8Array>;
/** Unwraps a wrapped content key and imports it as an AES-GCM CryptoKey. */
declare function unwrapUserKey(encryptedUserKey: string, kdfOutputBytes: Uint8Array, scheme?: KeyWrapScheme): Promise<CryptoKey>;
/**
 * Wraps a *deterministic* content key derived directly from `kdfOutputBytes`,
 * for EXISTING accounts migrating to the two-tier key model. The content key
 * equals the raw KDF output, so vault data previously encrypted directly under
 * that output remains readable without re-encryption. The KEK is still
 * domain-separated (HKDF `info`), so the wrapper is independent of the key.
 */
declare function createDeterministicWrappedUserKey(kdfOutputBytes: Uint8Array, scheme?: KeyWrapScheme): Promise<UserKeyBundle>;
/** Generates a fresh AES-256-GCM key and returns it as a JWK JSON string. */
declare function generateAesGcmKeyJwk(): Promise<string>;
/** Imports an AES-256-GCM key from a JWK JSON string for the given usages. */
declare function importAesGcmKeyFromJwk(jwkString: string, usages: ReadonlyArray<KeyUsage>): Promise<CryptoKey>;
/**
 * Re-wraps an existing content key under a new KDF output (new master password
 * / new salt). The content key itself is unchanged, so NO vault data is
 * re-encrypted — only the wrapper string changes.
 */
declare function rotateWrappedKey(encryptedUserKey: string, oldKdfOutputBytes: Uint8Array, newKdfOutputBytes: Uint8Array, scheme?: KeyWrapScheme): Promise<string>;

export { DEFAULT_KEY_WRAP_SCHEME, type KeyWrapScheme, type UserKeyBundle, createDeterministicWrappedUserKey, createWrappedUserKey, generateAesGcmKeyJwk, generateContentKeyBytes, importAesGcmKeyFromJwk, rotateWrappedKey, unwrapUserKey, unwrapUserKeyBytes };
