/**
 * Byte/string/base64 encoding helpers.
 *
 * These intentionally reproduce the exact base64 encoding used by Singra Vault
 * and Singra Premium so that DIS is byte-compatible with already-stored
 * ciphertext. Do not "optimise" the algorithm in a way that changes output.
 */
/**
 * Encodes bytes to a standard (RFC 4648) base64 string.
 *
 * Uses a chunked loop over `String.fromCharCode` + `btoa` to stay identical to
 * the legacy `uint8ArrayToBase64` implementation while avoiding call-stack
 * overflows on large inputs.
 */
declare function bytesToBase64(bytes: Uint8Array): string;
/** Decodes a standard base64 string to bytes. */
declare function base64ToBytes(base64: string): Uint8Array;
/** UTF-8 encodes a string to bytes. */
declare function utf8ToBytes(text: string): Uint8Array;
/** UTF-8 decodes bytes to a string. */
declare function bytesToUtf8(bytes: Uint8Array): string;
/**
 * Encodes bytes to an unpadded base64url (RFC 4648 §5) string.
 *
 * Reproduces the exact transform used by Singra Vault's `encodeBase64Url`
 * (standard base64 with `+`→`-`, `/`→`_`, trailing `=` stripped) so DIS is
 * byte-compatible with stored op-log signatures, hashes and public keys.
 */
declare function bytesToBase64Url(bytes: Uint8Array): string;
/** Decodes an unpadded base64url string to bytes. */
declare function base64UrlToBytes(base64url: string): Uint8Array;
/** Lower-case hex string of the given bytes. */
declare function bytesToHex(bytes: Uint8Array): string;
/** Concatenates byte arrays into a single new buffer. */
declare function concatBytes(...parts: Uint8Array[]): Uint8Array;

export { base64UrlToBytes as a, base64ToBytes as b, bytesToBase64 as c, bytesToBase64Url as d, bytesToHex as e, bytesToUtf8 as f, concatBytes as g, utf8ToBytes as u };
