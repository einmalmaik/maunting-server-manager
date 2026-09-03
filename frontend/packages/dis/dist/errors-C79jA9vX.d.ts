/**
 * Pluggable crypto provider abstraction.
 *
 * DIS does not invent cryptography. It binds to an audited primitive provider.
 * The default provider is the platform WebCrypto implementation
 * (`globalThis.crypto`), which is available in modern browsers and in Node >= 20.
 *
 * Exposing this as an interface keeps the door open for substituting a
 * hardware-backed or test provider without touching call sites, and keeps the
 * rest of DIS free of direct global access.
 */
/** Minimal subset of the WebCrypto API that DIS relies on. */
interface CryptoProvider {
    getRandomValues<T extends ArrayBufferView>(array: T): T;
    readonly subtle: SubtleCrypto;
}
/** Returns the active crypto provider, falling back to platform WebCrypto. */
declare function getCryptoProvider(): CryptoProvider;
/**
 * Overrides the active crypto provider. Intended for tests and for
 * environments that supply a non-global WebCrypto implementation.
 * Pass `null` to reset to platform auto-detection.
 */
declare function setCryptoProvider(provider: CryptoProvider | null): void;
/** Convenience accessor for `SubtleCrypto`. */
declare function subtle(): SubtleCrypto;

/**
 * Central, typed error hierarchy for DIS.
 *
 * Errors never include secret material (keys, plaintext, passwords) in their
 * message or properties. Callers may safely log `error.code` and `error.message`.
 */
type DisErrorCode = 'INVALID_ARGUMENT' | 'UNSUPPORTED_FORMAT_VERSION' | 'DECRYPTION_FAILED' | 'INTEGRITY_CHECK_FAILED' | 'KEY_DERIVATION_FAILED' | 'UNSUPPORTED_KDF_VERSION' | 'LEGACY_PAYLOAD_REQUIRES_MIGRATION' | 'PROVIDER_UNAVAILABLE' | 'USE_AFTER_DESTROY';
/** Base class for all errors thrown by DIS. */
declare class DisError extends Error {
    readonly code: DisErrorCode;
    constructor(code: DisErrorCode, message: string);
}
/** Thrown when an argument is missing or structurally invalid. */
declare class DisInvalidArgumentError extends DisError {
    constructor(message: string);
}
/**
 * Thrown when AEAD decryption or authentication fails. The cause (wrong key,
 * tampered ciphertext, or AAD mismatch) is intentionally not distinguished to
 * avoid leaking an oracle.
 */
declare class DisDecryptionError extends DisError {
    constructor(message?: string);
}
/** Thrown when a versioned payload carries a version DIS cannot read. */
declare class DisUnsupportedFormatVersionError extends DisError {
    constructor(message: string);
}
/** Thrown when an integrity / hash verification fails. */
declare class DisIntegrityError extends DisError {
    constructor(message?: string);
}
/** Thrown when a legacy, non-migratable payload is read on a runtime path. */
declare class DisLegacyPayloadError extends DisError {
    constructor(message?: string);
}

export { type CryptoProvider as C, DisDecryptionError as D, DisError as a, type DisErrorCode as b, DisIntegrityError as c, DisInvalidArgumentError as d, DisLegacyPayloadError as e, DisUnsupportedFormatVersionError as f, getCryptoProvider as g, subtle as h, setCryptoProvider as s };
