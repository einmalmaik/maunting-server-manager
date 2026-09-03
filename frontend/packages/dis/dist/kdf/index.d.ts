/**
 * dis-kdf — password-based key derivation.
 *
 * Primitive: Argon2id (via the audited `hash-wasm` implementation), with
 * versioned, immutable parameter sets so accounts can be transparently
 * upgraded to stronger parameters over time. Optional HKDF-Expand strengthening
 * binds the derived key to a second factor (e.g. a device key) without
 * weakening the password-derived material.
 *
 * DIS does not invent a KDF. Parameters follow OWASP Argon2id guidance.
 */
/** Argon2id parameter set. Once released, a version's params are immutable. */
interface KdfParams {
    /** Memory cost in KiB. */
    readonly memory: number;
    /** Iteration (time) cost. */
    readonly iterations: number;
    /** Degree of parallelism. */
    readonly parallelism: number;
    /** Output length in bytes. */
    readonly hashLength: number;
}
/**
 * Default versioned parameter registry. Byte-compatible with Singra Vault:
 *   v1: 64 MiB  (original baseline)
 *   v2: 128 MiB (current; exceeds OWASP Argon2id minimum)
 *
 * IMPORTANT: never change an existing version's parameters — only add versions.
 */
declare const DEFAULT_KDF_PARAMS: Readonly<Record<number, KdfParams>>;
/** The latest KDF version. New accounts derive with this version. */
declare const CURRENT_KDF_VERSION = 2;
/** Optional second-factor strengthening via HKDF-Expand (SHA-256). */
interface KdfStrengthenOptions {
    /** Salt for HKDF (e.g. a 256-bit device key). */
    readonly hkdfSalt: Uint8Array;
    /** Domain-separation `info` string for HKDF. Caller-owned (format contract). */
    readonly info: string;
}
interface DeriveRawKeyOptions {
    /** KDF version to look up in `params`. Defaults to {@link CURRENT_KDF_VERSION}. */
    readonly version?: number;
    /** Parameter registry. Defaults to {@link DEFAULT_KDF_PARAMS}. */
    readonly params?: Readonly<Record<number, KdfParams>>;
    /** Optional HKDF strengthening (e.g. device-key binding). */
    readonly strengthen?: KdfStrengthenOptions;
}
/** Generates a cryptographically secure, base64-encoded salt. */
declare function generateSalt(): string;
/**
 * Derives raw key bytes from a password using Argon2id (+ optional HKDF).
 *
 * The caller owns the returned buffer and MUST wipe it (`.fill(0)`).
 */
declare function deriveRawKey(password: string, saltBase64: string, options?: DeriveRawKeyOptions): Promise<Uint8Array>;
/** Imports raw 256-bit key bytes as a non-extractable AES-GCM CryptoKey. */
declare function importAesGcmKey(keyBytes: Uint8Array | BufferSource): Promise<CryptoKey>;
/**
 * Derives a non-extractable AES-GCM key from a password. Raw key bytes are
 * wiped as soon as the CryptoKey is imported.
 */
declare function deriveAesGcmKey(password: string, saltBase64: string, options?: DeriveRawKeyOptions): Promise<CryptoKey>;
/** Explicit Argon2id parameters for a single ad-hoc derivation. */
interface Argon2idRawParams {
    readonly password: string;
    readonly salt: Uint8Array;
    /** Memory cost in KiB. */
    readonly memorySize: number;
    readonly iterations: number;
    readonly parallelism: number;
    readonly hashLength: number;
}
/**
 * Low-level Argon2id derivation returning raw key bytes. This is the audited
 * `hash-wasm` Argon2id with caller-chosen parameters and a raw byte salt — for
 * call sites that derive a key with a context-specific salt/param set (device
 * key transfer wrapping, integrity HMAC key) rather than the versioned account
 * KDF registry used by {@link deriveRawKey}.
 */
declare function argon2idRaw(params: Argon2idRawParams): Promise<Uint8Array>;
/**
 * HKDF-SHA-256 expand/extract producing `lengthBits` of key material.
 *
 * `ikm` is the input keying material, `salt` defaults to the empty salt (the
 * op-log record/snapshot derivations use an empty salt and put all context in
 * `info`; the device-key derivation uses the device key as salt).
 */
declare function deriveHkdfSha256Bits(ikm: Uint8Array, options: {
    info: Uint8Array;
    salt?: Uint8Array;
    lengthBits?: number;
}): Promise<Uint8Array>;
/**
 * HKDF-SHA-256 deriving directly into a non-extractable AES-256-GCM key,
 * mirroring `crypto.subtle.deriveKey` (used by passkey PRF wrapping and the
 * legacy device-key wrapping path).
 */
declare function deriveHkdfAesGcmKey(ikm: Uint8Array, options: {
    info: Uint8Array;
    salt?: Uint8Array;
    usages?: KeyUsage[];
}): Promise<CryptoKey>;

export { type Argon2idRawParams, CURRENT_KDF_VERSION, DEFAULT_KDF_PARAMS, type DeriveRawKeyOptions, type KdfParams, type KdfStrengthenOptions, argon2idRaw, deriveAesGcmKey, deriveHkdfAesGcmKey, deriveHkdfSha256Bits, deriveRawKey, generateSalt, importAesGcmKey };
