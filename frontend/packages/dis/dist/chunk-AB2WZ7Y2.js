import { subtle } from './chunk-CYIGDF63.js';
import { DisInvalidArgumentError } from './chunk-MJO7IJZC.js';

// src/signing/index.ts
var ECDSA_P256_ALGORITHM = { name: "ECDSA", namedCurve: "P-256" };
var ECDSA_P256_PARAMS = { name: "ECDSA", hash: "SHA-256" };
var ECDSA_P256_SIGNATURE_LENGTH = 64;
async function generateEcdsaP256KeyPair() {
  const keyPair = await subtle().generateKey(
    ECDSA_P256_ALGORITHM,
    /* extractable */
    false,
    ["sign", "verify"]
  );
  const spki = await subtle().exportKey("spki", keyPair.publicKey);
  return {
    privateKey: keyPair.privateKey,
    publicKey: keyPair.publicKey,
    publicKeySpki: new Uint8Array(spki)
  };
}
async function importEcdsaP256PublicKeySpki(spki) {
  return subtle().importKey(
    "spki",
    spki,
    ECDSA_P256_ALGORITHM,
    false,
    ["verify"]
  );
}
async function signEcdsaP256(privateKey, data) {
  const signature = await subtle().sign(ECDSA_P256_PARAMS, privateKey, data);
  const bytes = new Uint8Array(signature);
  if (bytes.length !== ECDSA_P256_SIGNATURE_LENGTH) {
    throw new DisInvalidArgumentError("unexpected ECDSA signature byte length");
  }
  return bytes;
}
async function verifyEcdsaP256(publicKey, signature, data) {
  if (signature.length !== ECDSA_P256_SIGNATURE_LENGTH) {
    throw new DisInvalidArgumentError("unexpected ECDSA signature byte length");
  }
  try {
    return await subtle().verify(
      ECDSA_P256_PARAMS,
      publicKey,
      signature,
      data
    );
  } catch {
    return false;
  }
}

export { ECDSA_P256_SIGNATURE_LENGTH, generateEcdsaP256KeyPair, importEcdsaP256PublicKeySpki, signEcdsaP256, verifyEcdsaP256 };
//# sourceMappingURL=chunk-AB2WZ7Y2.js.map
//# sourceMappingURL=chunk-AB2WZ7Y2.js.map