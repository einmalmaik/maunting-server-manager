import { utf8ToBytes, bytesToBase64, base64ToBytes, bytesToUtf8 } from './chunk-JSKIWIEC.js';
import { subtle } from './chunk-CYIGDF63.js';

// src/asymmetric/index.ts
var RSA_OAEP_MODULUS_LENGTH = 4096;
var RSA_OAEP_ALGORITHM = {
  name: "RSA-OAEP",
  hash: "SHA-256"
};
async function generateRsaOaepKeyPair() {
  return subtle().generateKey(
    {
      name: "RSA-OAEP",
      modulusLength: RSA_OAEP_MODULUS_LENGTH,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256"
    },
    true,
    ["encrypt", "decrypt"]
  );
}
async function exportJwk(key) {
  return subtle().exportKey("jwk", key);
}
async function importRsaOaepPublicKey(jwk) {
  return subtle().importKey("jwk", jwk, RSA_OAEP_ALGORITHM, true, ["encrypt"]);
}
async function importRsaOaepPrivateKey(jwk) {
  return subtle().importKey("jwk", jwk, RSA_OAEP_ALGORITHM, false, ["decrypt"]);
}
async function rsaOaepEncrypt(plaintext, publicKey) {
  const encoded = utf8ToBytes(plaintext);
  try {
    const encrypted = await subtle().encrypt({ name: "RSA-OAEP" }, publicKey, encoded);
    return bytesToBase64(new Uint8Array(encrypted));
  } finally {
    encoded.fill(0);
  }
}
async function rsaOaepDecrypt(ciphertextBase64, privateKey) {
  const encrypted = base64ToBytes(ciphertextBase64);
  let plaintextBytes = null;
  try {
    const decrypted = await subtle().decrypt({ name: "RSA-OAEP" }, privateKey, encrypted);
    plaintextBytes = new Uint8Array(decrypted);
    return bytesToUtf8(plaintextBytes);
  } finally {
    encrypted.fill(0);
    plaintextBytes?.fill(0);
  }
}

export { RSA_OAEP_MODULUS_LENGTH, exportJwk, generateRsaOaepKeyPair, importRsaOaepPrivateKey, importRsaOaepPublicKey, rsaOaepDecrypt, rsaOaepEncrypt };
//# sourceMappingURL=chunk-KNCZMIZA.js.map
//# sourceMappingURL=chunk-KNCZMIZA.js.map