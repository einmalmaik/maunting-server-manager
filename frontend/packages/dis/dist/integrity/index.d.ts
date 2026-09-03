/**
 * dis-integrity — hashing and verification helpers.
 *
 * Provides SHA-256 digests (base64) and constant-time comparison. Used for
 * file-chunk integrity, manifest roots, and any place that must verify a
 * payload has not been altered. Confidential payloads still rely on AEAD;
 * these helpers cover integrity of already-encrypted / public material.
 */
/** SHA-256 of raw bytes, returned as a fresh `Uint8Array` digest. */
declare function sha256Bytes(data: Uint8Array): Promise<Uint8Array>;
/** SHA-256 of raw bytes, base64-encoded. */
declare function sha256Base64(data: Uint8Array): Promise<string>;
/** SHA-256 of raw bytes, unpadded-base64url-encoded. */
declare function sha256Base64Url(data: Uint8Array): Promise<string>;
/** SHA-256 of raw bytes, lower-case hex-encoded. */
declare function sha256Hex(data: Uint8Array): Promise<string>;
/**
 * SHA-1 of raw bytes, lower-case hex-encoded.
 *
 * SHA-1 is collision-broken and MUST NOT be used for any security decision.
 * It is provided solely for legacy interop where a remote protocol mandates
 * it — specifically the HaveIBeenPwned k-anonymity range API, which keys on
 * the SHA-1 of the password. Callers uppercase the hex as the API requires.
 */
declare function sha1Hex(data: Uint8Array): Promise<string>;
/** Imports raw bytes as an HMAC-SHA-256 key. */
declare function importHmacSha256Key(keyBytes: Uint8Array, usages?: KeyUsage[]): Promise<CryptoKey>;
/** Computes HMAC-SHA-256 over `data` with an already-imported key. */
declare function hmacSha256WithKey(key: CryptoKey, data: Uint8Array): Promise<Uint8Array>;
/** Computes HMAC-SHA-256 over `data` with raw key bytes. */
declare function hmacSha256(keyBytes: Uint8Array, data: Uint8Array): Promise<Uint8Array>;
/** SHA-256 of a UTF-8 string, base64-encoded. */
declare function sha256StringBase64(data: string): Promise<string>;
/** SHA-256 of the JSON serialisation of `value`, base64-encoded. */
declare function sha256JsonBase64(value: unknown): Promise<string>;
/** Constant-time equality of two byte arrays. */
declare function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean;
/** Constant-time equality of two base64 strings (compared as decoded bytes). */
declare function constantTimeEqualBase64(a: string, b: string): boolean;
/**
 * Verifies that `data` hashes (SHA-256) to `expectedBase64`, throwing
 * {@link DisIntegrityError} on mismatch. Comparison is constant-time.
 */
declare function verifyPayloadIntegrity(data: Uint8Array, expectedBase64: string): Promise<void>;

export { constantTimeEqual, constantTimeEqualBase64, hmacSha256, hmacSha256WithKey, importHmacSha256Key, sha1Hex, sha256Base64, sha256Base64Url, sha256Bytes, sha256Hex, sha256JsonBase64, sha256StringBase64, verifyPayloadIntegrity };
