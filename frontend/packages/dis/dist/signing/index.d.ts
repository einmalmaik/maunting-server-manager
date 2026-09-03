/**
 * dis-signing — asymmetric digital signatures.
 *
 * Primitive: ECDSA over the NIST P-256 curve with SHA-256, via WebCrypto.
 * Public keys are exchanged as SPKI bytes; private keys are generated
 * non-extractable so they never leave the device. Signatures are the raw
 * `r || s` concatenation WebCrypto produces (fixed 64 bytes for P-256), which
 * is the exact wire form Singra Vault's op-log device signatures use.
 *
 * Canonicalisation of the signed payload is the caller's responsibility — DIS
 * signs and verifies opaque byte strings and never interprets their structure.
 */
/** Raw byte length of a P-256 ECDSA signature (`r || s`, 32 bytes each). */
declare const ECDSA_P256_SIGNATURE_LENGTH = 64;
interface EcdsaP256KeyPair {
    readonly privateKey: CryptoKey;
    readonly publicKey: CryptoKey;
    /** SPKI-encoded public key bytes, ready to be base64url-encoded for storage. */
    readonly publicKeySpki: Uint8Array;
}
/**
 * Generates a fresh non-extractable ECDSA P-256 key pair and exports the
 * public key as SPKI bytes.
 */
declare function generateEcdsaP256KeyPair(): Promise<EcdsaP256KeyPair>;
/** Imports an SPKI-encoded P-256 public key for signature verification. */
declare function importEcdsaP256PublicKeySpki(spki: Uint8Array): Promise<CryptoKey>;
/**
 * Signs `data` with an ECDSA P-256 private key, returning the raw 64-byte
 * `r || s` signature. Throws {@link DisInvalidArgumentError} if WebCrypto
 * returns an unexpected length (defends against curve/algorithm misuse).
 */
declare function signEcdsaP256(privateKey: CryptoKey, data: Uint8Array): Promise<Uint8Array>;
/**
 * Verifies a raw 64-byte `r || s` ECDSA P-256 signature over `data`. Returns
 * `false` for an invalid signature; throws {@link DisInvalidArgumentError} if
 * the signature is not the expected length.
 */
declare function verifyEcdsaP256(publicKey: CryptoKey, signature: Uint8Array, data: Uint8Array): Promise<boolean>;

export { ECDSA_P256_SIGNATURE_LENGTH, type EcdsaP256KeyPair, generateEcdsaP256KeyPair, importEcdsaP256PublicKeySpki, signEcdsaP256, verifyEcdsaP256 };
