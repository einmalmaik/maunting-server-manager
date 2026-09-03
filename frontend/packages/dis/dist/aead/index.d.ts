/**
 * dis-aead — authenticated encryption with associated data.
 *
 * Primitive: AES-256-GCM via WebCrypto. Output format is
 * `base64(IV(12) || ciphertext || authTag(16))`, byte-compatible with Singra
 * Vault. A fresh random 96-bit IV is generated per call. Associated data (AAD)
 * is authenticated but not stored; the same AAD must be supplied on decrypt,
 * which lets callers bind ciphertext to a context (e.g. an entry id) and defeat
 * ciphertext-swap attacks.
 */
/** Encrypts bytes with AES-256-GCM. Caller still owns/wipes `plaintextBytes`. */
declare function encryptBytes(plaintextBytes: Uint8Array, key: CryptoKey, associatedData?: string): Promise<string>;
/** Decrypts AES-256-GCM bytes. Returned buffer is secret — caller must wipe it. */
declare function decryptBytes(encryptedBase64: string, key: CryptoKey, associatedData?: string): Promise<Uint8Array>;
/** Imports raw key bytes as an AES-GCM key with the given usages. */
declare function importAesGcmRawKey(keyBytes: Uint8Array, usages: KeyUsage[]): Promise<CryptoKey>;
/** Generates a fresh non-extractable AES-256-GCM key. */
declare function generateAesGcmKey(usages?: KeyUsage[], extractable?: boolean): Promise<CryptoKey>;
/**
 * AES-256-GCM encrypt with a caller-supplied nonce and optional binary AAD.
 * Returns raw `ciphertext || authTag` bytes (no nonce prefix). `key` may be a
 * raw 32-byte array or an already-imported `CryptoKey`.
 */
declare function aesGcmEncrypt(key: CryptoKey | Uint8Array, nonce: Uint8Array, plaintext: Uint8Array, associatedData?: Uint8Array): Promise<Uint8Array>;
/**
 * AES-256-GCM decrypt with a caller-supplied nonce and optional binary AAD.
 * `ciphertext` is raw `ciphertext || authTag` bytes. Throws
 * {@link DisDecryptionError} on any failure (wrong key / tamper / AAD mismatch)
 * without distinguishing the cause.
 */
declare function aesGcmDecrypt(key: CryptoKey | Uint8Array, nonce: Uint8Array, ciphertext: Uint8Array, associatedData?: Uint8Array): Promise<Uint8Array>;
/** Encrypts a UTF-8 string. The intermediate plaintext bytes are wiped. */
declare function encryptString(plaintext: string, key: CryptoKey, associatedData?: string): Promise<string>;
/** Decrypts to a UTF-8 string. The intermediate plaintext bytes are wiped. */
declare function decryptString(encryptedBase64: string, key: CryptoKey, associatedData?: string): Promise<string>;

export { aesGcmDecrypt, aesGcmEncrypt, decryptBytes, decryptString, encryptBytes, encryptString, generateAesGcmKey, importAesGcmRawKey };
