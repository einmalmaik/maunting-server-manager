/**
 * DIS — Defensive Integration Shield · post-quantum hybrid key wrapping.
 * Powered by DIS — Defensive Integration Shield.
 *
 * Hybrid key wrapping combining:
 * - ML-KEM-768 (FIPS 203) for post-quantum key encapsulation
 * - RSA-4096-OAEP for classical encryption
 *
 * In the product threat model this protects sharing and emergency-access
 * keys against "harvest now, decrypt later" attacks. It is NOT the encryption
 * layer for vault item payloads, which remain AES-256-GCM encrypted with
 * user-derived symmetric keys (see `@dis/shield/vault-encryption`).
 *
 * Wire format is preserved byte-for-byte from the legacy Singra implementation:
 *   version(1) || pq_ct(1088) || rsa_ct(512) || iv(12) || aes_ct(variable)
 * with version bytes 0x01 (RSA-only), 0x02 (legacy hybrid), 0x03 (HKDF-v1),
 * 0x04 (HKDF-v2, current). Changing any constant below breaks decryption of
 * already-stored sharing/emergency keys — treat as a hard compatibility gate.
 *
 * @see https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf
 */
/**
 * Product-wide security standard version. Centralized so all hybrid/PQ
 * sharing-key provisioning and runtime enforcement paths stay in sync.
 */
declare const SECURITY_STANDARD_VERSION = 1;
/** Current hybrid ciphertext version (v2 HKDF construction) */
declare const HYBRID_VERSION = 4;
interface SharedKeyWrapAadInput {
    collectionId: string;
    senderUserId: string;
    recipientUserId: string;
    keyVersion: string | number;
}
declare function buildSharedKeyWrapAad(input: SharedKeyWrapAadInput): string;
/**
 * Generates a new ML-KEM-768 key pair.
 *
 * @returns Object with base64-encoded public and secret keys
 */
declare function generatePQKeyPair(): PQKeyPair;
/**
 * Generates a hybrid key pair combining ML-KEM-768 and RSA-4096.
 *
 * @returns Object with both PQ and RSA keys
 */
declare function generateHybridKeyPair(): Promise<HybridKeyPair>;
/**
 * Encrypts key material using hybrid ML-KEM-768 + RSA-4096-OAEP encryption.
 *
 * The supplied key material is encrypted with a randomly generated AES-256 key.
 * This AES key is then encapsulated/encrypted with both:
 * 1. ML-KEM-768 (post-quantum KEM)
 * 2. RSA-4096-OAEP (classically secure)
 *
 * Format: version(1) || pq_ciphertext(1088) || rsa_ciphertext(512) || iv(12) || aes_ciphertext(variable)
 *
 * @param plaintext - Serialized key material to wrap
 * @param pqPublicKey - Base64-encoded ML-KEM-768 public key
 * @param rsaPublicKey - JWK string of RSA-4096 public key
 * @param aad - Optional additional authenticated data for AES-GCM context binding
 * @returns Base64-encoded hybrid ciphertext
 */
declare function hybridEncrypt(plaintext: string, pqPublicKey: string, rsaPublicKey: string, aad?: string): Promise<string>;
/**
 * Decrypts hybrid ML-KEM-768 + RSA-4096-OAEP wrapped key material.
 *
 * @param ciphertextBase64 - Base64-encoded hybrid ciphertext
 * @param pqSecretKey - Base64-encoded ML-KEM-768 secret key
 * @param rsaPrivateKey - JWK string of RSA-4096 private key
 * @param aad - Optional additional authenticated data (must match encrypt-time AAD)
 * @returns Decrypted plaintext
 * @throws Error if decryption fails or version is unsupported
 */
declare function hybridDecrypt(ciphertextBase64: string, pqSecretKey: string, rsaPrivateKey: string, aad?: string): Promise<string>;
/**
 * Wraps a shared AES key using hybrid encryption.
 * Used for shared collections where each member gets a wrapped copy.
 *
 * @param sharedKeyJwk - JWK string of the shared AES-256 key
 * @param pqPublicKey - Base64-encoded ML-KEM-768 public key
 * @param rsaPublicKey - JWK string of RSA-4096 public key
 * @param aad - Optional additional authenticated data (e.g. collection ID)
 * @returns Base64-encoded wrapped key
 */
declare function hybridWrapKey(sharedKeyJwk: string, pqPublicKey: string, rsaPublicKey: string, aad: string): Promise<string>;
/**
 * Unwraps a shared AES key using hybrid decryption.
 *
 * @param wrappedKey - Base64-encoded wrapped key
 * @param pqSecretKey - Base64-encoded ML-KEM-768 secret key
 * @param rsaPrivateKey - JWK string of RSA-4096 private key
 * @param aad - Optional additional authenticated data (must match wrap-time AAD)
 * @returns JWK string of the shared AES-256 key
 */
declare function hybridUnwrapKey(wrappedKey: string, pqSecretKey: string, rsaPrivateKey: string, aad: string): Promise<string>;
/**
 * Checks if wrapped key material uses any hybrid (post-quantum) encryption version.
 * Recognizes legacy hybrid (0x02), standard v1 (0x03), and standard v2 (0x04).
 *
 * @param ciphertextBase64 - Base64-encoded ciphertext
 * @returns true if any hybrid wrapped-key version, false if legacy RSA-only or unknown
 */
declare function isHybridEncrypted(ciphertextBase64: string): boolean;
/**
 * Checks if a ciphertext uses the current standard encryption (v2 HKDF, version 0x04).
 * Use this for security enforcement checks where only the latest format is acceptable.
 *
 * @param ciphertextBase64 - Base64-encoded ciphertext
 * @returns true if current standard (0x04), false otherwise
 */
declare function isCurrentStandardEncrypted(ciphertextBase64: string): boolean;
/**
 * Re-wraps legacy RSA-only or older hybrid key material with current hybrid key wrapping (v2).
 * Used during migration to post-quantum protection for sharing and emergency keys.
 *
 * - Version 0x04: already current, returned unchanged.
 * - Version 0x03: decrypted with legacy HKDF-v1, re-encrypted with HKDF-v2.
 * - Version 0x02: decrypted with legacy hybrid path, re-encrypted.
 * - Version 0x01 / unknown: decrypted with RSA-only, re-encrypted.
 *
 * @param legacyCiphertext - Base64-encoded legacy ciphertext
 * @param rsaPrivateKey - JWK string of RSA private key for decryption
 * @param pqSecretKey - Base64-encoded ML-KEM-768 secret key (required for hybrid legacy)
 * @param pqPublicKey - Base64-encoded ML-KEM-768 public key
 * @param rsaPublicKey - JWK string of RSA public key
 * @returns Base64-encoded hybrid ciphertext (version 0x04)
 */
declare function migrateToHybrid(legacyCiphertext: string, rsaPrivateKey: string, pqSecretKey: string | null, pqPublicKey: string, rsaPublicKey: string, aad?: string): Promise<string>;
/**
 * ML-KEM-768 key pair
 */
interface PQKeyPair {
    /** Base64-encoded ML-KEM-768 public key (1184 bytes) */
    publicKey: string;
    /** Base64-encoded ML-KEM-768 secret key (2400 bytes) */
    secretKey: string;
}
/**
 * Combined hybrid key pair with both PQ and classical keys
 */
interface HybridKeyPair {
    /** Base64-encoded ML-KEM-768 public key */
    pqPublicKey: string;
    /** Base64-encoded ML-KEM-768 secret key */
    pqSecretKey: string;
    /** JWK string of RSA-4096 public key */
    rsaPublicKey: string;
    /** JWK string of RSA-4096 private key */
    rsaPrivateKey: string;
}

export { HYBRID_VERSION, type HybridKeyPair, type PQKeyPair, SECURITY_STANDARD_VERSION, type SharedKeyWrapAadInput, buildSharedKeyWrapAad, generateHybridKeyPair, generatePQKeyPair, hybridDecrypt, hybridEncrypt, hybridUnwrapKey, hybridWrapKey, isCurrentStandardEncrypted, isHybridEncrypted, migrateToHybrid };
