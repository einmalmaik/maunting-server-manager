// src/core/encoding.ts
function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 32768;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}
function base64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
var textEncoder = new TextEncoder();
var textDecoder = new TextDecoder();
function utf8ToBytes(text) {
  return textEncoder.encode(text);
}
function bytesToUtf8(bytes) {
  return textDecoder.decode(bytes);
}
function bytesToBase64Url(bytes) {
  return bytesToBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function base64UrlToBytes(base64url) {
  const normalized = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  return base64ToBytes(padded);
}
function bytesToHex(bytes) {
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}
function concatBytes(...parts) {
  let total = 0;
  for (const part of parts) total += part.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

export { base64ToBytes, base64UrlToBytes, bytesToBase64, bytesToBase64Url, bytesToHex, bytesToUtf8, concatBytes, utf8ToBytes };
//# sourceMappingURL=chunk-JSKIWIEC.js.map
//# sourceMappingURL=chunk-JSKIWIEC.js.map