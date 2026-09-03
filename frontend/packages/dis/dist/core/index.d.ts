export { C as CryptoProvider, D as DisDecryptionError, a as DisError, b as DisErrorCode, c as DisIntegrityError, d as DisInvalidArgumentError, e as DisLegacyPayloadError, f as DisUnsupportedFormatVersionError, g as getCryptoProvider, s as setCryptoProvider, h as subtle } from '../errors-C79jA9vX.js';
export { b as base64ToBytes, a as base64UrlToBytes, c as bytesToBase64, d as bytesToBase64Url, e as bytesToHex, f as bytesToUtf8, g as concatBytes, u as utf8ToBytes } from '../encoding-B-cb7Duu.js';

/**
 * Cryptographic constants shared across DIS modules.
 *
 * These values are part of the on-the-wire / on-disk format contract. Changing
 * any released constant is a breaking format change and MUST be expressed as a
 * new format version, never an in-place edit.
 */
/** AES-GCM IV length in bytes (96 bits — the recommended GCM nonce size). */
declare const AES_GCM_IV_LENGTH = 12;
/** AES-GCM authentication tag length in bits. */
declare const AES_GCM_TAG_LENGTH = 128;
/** Symmetric key length in bytes (AES-256). */
declare const AES_KEY_LENGTH = 32;
/** Salt length in bytes (128 bits) for password-based key derivation. */
declare const KDF_SALT_LENGTH = 16;

export { AES_GCM_IV_LENGTH, AES_GCM_TAG_LENGTH, AES_KEY_LENGTH, KDF_SALT_LENGTH };
