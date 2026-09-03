/**
 * dis-asymmetric — RSA-OAEP public-key operations.
 *
 * Primitive: RSA-OAEP (4096-bit modulus, SHA-256) via WebCrypto. Used by the
 * Singra sharing / emergency-access profile to wrap symmetric material for a
 * recipient's public key. DIS does not invent any asymmetric scheme — this is
 * a thin, audited wrapper over WebCrypto so applications never touch the raw
 * `crypto.subtle` surface.
 *
 * Key material is exported/imported as JWK (the format Singra persists), so
 * existing stored keys remain byte-compatible. Wire format for ciphertext is
 * `base64(rsa_oaep_output)`, identical to the legacy implementation.
 */
/** RSA-OAEP modulus length in bits. Part of the key-generation format contract. */
declare const RSA_OAEP_MODULUS_LENGTH = 4096;
/**
 * Generates an extractable RSA-OAEP-4096 key pair (SHA-256, e=65537).
 * Extractable so the private key can be exported as JWK and wrapped at rest.
 */
declare function generateRsaOaepKeyPair(): Promise<CryptoKeyPair>;
/** Exports an RSA key (public or private) as a JWK object. */
declare function exportJwk(key: CryptoKey): Promise<JsonWebKey>;
/** Imports an RSA-OAEP public key (JWK) for `encrypt`. Extractable. */
declare function importRsaOaepPublicKey(jwk: JsonWebKey): Promise<CryptoKey>;
/** Imports an RSA-OAEP private key (JWK) for `decrypt`. Non-extractable. */
declare function importRsaOaepPrivateKey(jwk: JsonWebKey): Promise<CryptoKey>;
/** Encrypts a UTF-8 string under an RSA-OAEP public key. Returns base64. */
declare function rsaOaepEncrypt(plaintext: string, publicKey: CryptoKey): Promise<string>;
/** Decrypts base64 RSA-OAEP ciphertext under an RSA-OAEP private key. */
declare function rsaOaepDecrypt(ciphertextBase64: string, privateKey: CryptoKey): Promise<string>;

export { RSA_OAEP_MODULUS_LENGTH, exportJwk, generateRsaOaepKeyPair, importRsaOaepPrivateKey, importRsaOaepPublicKey, rsaOaepDecrypt, rsaOaepEncrypt };
