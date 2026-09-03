import { DisError } from './chunk-MJO7IJZC.js';

// src/core/provider.ts
var activeProvider = null;
function resolvePlatformProvider() {
  const candidate = globalThis.crypto;
  if (!candidate || typeof candidate.getRandomValues !== "function" || !candidate.subtle) {
    throw new DisError(
      "PROVIDER_UNAVAILABLE",
      "No WebCrypto provider available. Provide one via setCryptoProvider()."
    );
  }
  return candidate;
}
function getCryptoProvider() {
  if (!activeProvider) {
    activeProvider = resolvePlatformProvider();
  }
  return activeProvider;
}
function setCryptoProvider(provider) {
  activeProvider = provider;
}
function subtle() {
  return getCryptoProvider().subtle;
}

export { getCryptoProvider, setCryptoProvider, subtle };
//# sourceMappingURL=chunk-CYIGDF63.js.map
//# sourceMappingURL=chunk-CYIGDF63.js.map